import traceback
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any

from database import fetch_all_data, save_schedule
from scheduler_engine import SchoolScheduler
from ai_agent import AIAgent

app = FastAPI(title="School Scheduler API v3 Relational")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ai_agent = AIAgent()

class GenerateRequest(BaseModel):
    randomize: bool = False

class AdjustRequest(BaseModel):
    command: str

def prepare_assignments_as_blocks(assignments: List[Dict], subjects: List[Dict], years: List[Dict], config: Dict) -> List[Dict]:
    max_block = config.get("max_block_duration", 3)
    schedulable_blocks = []
    
    subj_map = {s["id"]: s for s in subjects}
    year_map = {y["id"]: y for y in years}

    for assign in assignments:
        sks = int(assign.get("sks", 2))
        assign_id = assign.get("id")
        
        s_id = assign["subject_id"]
        t_id = assign["teacher_id"]
        y_id = assign["year_id"]
        
        s_name = subj_map.get(s_id, {}).get("name", "Unknown Subject")
        y_name = year_map.get(y_id, {}).get("name", "Unknown Year")

        remaining_sks = sks
        part_counter = 1

        while remaining_sks > 0:
            if remaining_sks >= max_block:
                duration = max_block
            else:
                duration = remaining_sks
            
            block = {
                "block_id": f"{assign_id}_p{part_counter}",
                "teacher_id": t_id,
                "subject_id": s_id,      # Added: Critical for Subject Constraints
                "year_id": y_id,
                "subject_name": s_name,
                "year_name": y_name,
                "duration": duration
            }
            
            schedulable_blocks.append(block)
            remaining_sks -= duration
            part_counter += 1

    print(f"🔄 Converted {len(assignments)} Assigned Classes into {len(schedulable_blocks)} Schedulable Blocks.")
    return schedulable_blocks

@app.post("/generate")
def generate_schedule(request: GenerateRequest):
    try:
        data = fetch_all_data()
        
        if not data.get("assigned_classes"):
            return {"status": "error", "message": "No assignments found."}

        blocks = prepare_assignments_as_blocks(
            data["assigned_classes"], 
            data["subjects"], 
            data["academic_years"],
            data["config"]
        )
        data["blocks"] = blocks 

        engine = SchoolScheduler(data)
        result = engine.solve(randomize=request.randomize)

        if result["status"] == "success":
            save_schedule(result["data"])
            return {
                "status": "success", 
                "message": "Schedule Generated",
                "stats": {
                    "assignments": len(data["assigned_classes"]),
                    "blocks_scheduled": len(result["data"])
                }
            }
        else:
            analysis = ai_agent.analyze_solver_failure(data, result.get("error"))
            return {
                "status": "failure",
                "error": result.get("error"),
                "ai_analysis": analysis
            }

    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/adjust")
def adjust_schedule(request: AdjustRequest):
    try:
        ai_res = ai_agent.process_command(request.command)
        if ai_res["status"] == "error":
            return ai_res

        data = fetch_all_data()
        blocks = prepare_assignments_as_blocks(
            data["assigned_classes"], 
            data["subjects"], 
            data["academic_years"],
            data["config"]
        )
        data["blocks"] = blocks
        
        engine = SchoolScheduler(data)
        result = engine.solve()

        if result["status"] == "success":
            save_schedule(result["data"])
            return {"status": "success", "message": f"Adjusted: {ai_res['message']}"}
        else:
             return {"status": "warning", "message": "Constraint saved, but schedule generation failed."}

    except Exception as e:
        return {"status": "error", "message": str(e)}