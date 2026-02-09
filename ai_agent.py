import os
import random
from typing import List, Literal, Dict, Any
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from firebase_admin import firestore # Import for DELETE_FIELD
from dotenv import load_dotenv

# Assumes these exist in your project structure
from database import db, fetch_all_data 

load_dotenv()

# ---------------- CONFIG ----------------

API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-3-flash-preview" 

# --- SCHEMAS (STRICT & FLAT) ---

# FIX: Removed "Optional". Used strict types with default values ("" or [])
# to prevent SDK validation errors regarding "NULL" types.
class SchedulerAction(BaseModel):
    action: Literal[
        "block_teacher", 
        "block_room", 
        "block_subject", 
        "unblock_subject", 
        "unblock_teacher", 
        "unblock_room", 
        "force_subject", 
        "clear_all_constraints", 
        "general_constraint"
    ]
    
    # Defaults ensure the API never sees "null"
    teacher_id: str = Field("", description="ID of the teacher if relevant, else empty string")
    room_id: str = Field("", description="ID of the room if relevant, else empty string")
    subject_id: str = Field("", description="ID of the subject if relevant, else empty string")
    
    slot_ids: List[str] = Field(default_factory=list, description="List of slots to block/unblock")
    
    target_slot_id: str = Field("", description="Target slot for force actions (e.g. 'Mon_1')")
    confirmation: bool = Field(False, description="True if user wants to reset/clear all")
    description: str = Field("", description="General description if needed")

# --- AGENT ---

class AIAgent:
    def get_context(self) -> Dict:
        data = fetch_all_data()
        return {
            "teachers": {t["id"]: t["name"] for t in data.get("teachers", [])},
            "rooms": {r["id"]: r["name"] for r in data.get("rooms", [])},
            "subjects": {s["id"]: s["name"] for s in data.get("subjects", [])},
        }

    def process_command(self, user_text: str) -> dict:
        ctx = self.get_context()
        
        prompt = f"""
        You are a School Scheduler Assistant.
        
        CONTEXT:
        Subjects: {ctx['subjects']}
        Teachers: {ctx['teachers']}
        Rooms: {ctx['rooms']}
        
        INSTRUCTIONS:
        Determine the intent and extract the parameters into the JSON schema.
        If a field is not relevant to the action, leave it as an empty string "" or empty list [].
        
        1. **BLOCK**: "busy", "unavailable", "can't", "don't put" -> 'block_*'
        2. **UNBLOCK**: "free", "available", "remove restriction" -> 'unblock_*'
        3. **MOVE/FORCE**: "Move [Subject] to [Day] [Slot]", "Must start at..." -> 'force_subject'.
        4. **RESET**: "Remove all constraints", "Clear everything", "Reset" -> 'clear_all_constraints'.
        
        USER COMMAND: "{user_text}"
        """
        
        try:
            # We pass the flat SchedulerAction directly as the schema
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SchedulerAction
                )
            )
            
            # The SDK now returns the SchedulerAction instance directly
            parsed_action = response.parsed 
            
            if not parsed_action:
                 return {"status": "error", "message": "I couldn't understand that command."}

            return self.execute_action(parsed_action, ctx)
            
        except Exception as e:
            print(f"Agent Error: {e}")
            return {"status": "error", "message": f"I got confused! Error: {str(e)}"}

    def expand_slots(self, slot_ids: List[str]) -> List[str]:
        final_slots = []
        if not slot_ids: return []
        for s in slot_ids:
            if len(s) == 3 and "_" not in s: # e.g. "Mon"
                final_slots.extend([f"{s}_{i}" for i in range(1, 9)])
            else:
                final_slots.append(s)
        return list(set(final_slots))

    def execute_action(self, action_obj: SchedulerAction, ctx: Dict):
        
        # --- HELPER: UPDATER ---
        def update_constraint(collection, doc_id, slots, mode="block"):
            # Check for empty string instead of None
            if not doc_id: return None, "Missing ID."
            
            ref = db.collection(collection).document(doc_id)
            doc = ref.get()
            if not doc.exists: return None, f"I couldn't find ID {doc_id} in the database."
            
            current = doc.to_dict().get("unavailable_slots", [])
            if mode == "block":
                updated = list(set(current + slots))
            else:
                updated = [s for s in current if s not in slots]
                
            ref.update({"unavailable_slots": updated})
            return True, "Updated"

        # --- HELPER: RESETTER ---
        def wipe_collection_constraints(collection_name, fields_to_reset):
            batch = db.batch()
            count = 0
            docs = db.collection(collection_name).stream()
            
            for doc in docs:
                batch.update(doc.reference, fields_to_reset)
                count += 1
                if count >= 400: 
                    batch.commit()
                    batch = db.batch()
                    count = 0
            
            if count > 0:
                batch.commit()

        # --- RESPONSE MESSAGES ---
        def get_reset_msg():
            msgs = [
                " I've wiped all constraints. We are starting fresh.",
                "Done. I've cleared every restriction for Teachers, Rooms, and Subjects.",
                "Reset complete",
            ]
            return random.choice(msgs)

        def get_move_msg(name, slot):
            msgs = [
                f"I've pinned **{name}** to start exactly at **{slot}**.",
                f"Moved! **{name}** is locked to start at **{slot}**.",
            ]
            return random.choice(msgs)

        # --- HANDLERS ---

        if action_obj.action == "clear_all_constraints":
            try:
                wipe_collection_constraints("teachers", {"unavailable_slots": []})
                wipe_collection_constraints("rooms", {"unavailable_slots": []})
                wipe_collection_constraints("subjects", {
                    "unavailable_slots": [], 
                    "fixed_slot": firestore.DELETE_FIELD
                })
                return {"status": "success", "message": get_reset_msg()}
            except Exception as e:
                return {"status": "error", "message": f"Failed to reset: {str(e)}"}

        if action_obj.action == "block_teacher":
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("teachers", action_obj.teacher_id, slots, "block")
            if not ok: return {"status": "error", "message": err}
            name = ctx["teachers"].get(action_obj.teacher_id, "that teacher")
            return {"status": "success", "message": f"Blocked **{name}** for {len(slots)} slots."}

        if action_obj.action == "unblock_teacher":
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("teachers", action_obj.teacher_id, slots, "unblock")
            if not ok: return {"status": "error", "message": err}
            name = ctx["teachers"].get(action_obj.teacher_id, "that teacher")
            return {"status": "success", "message": f"Freed up **{name}** on {len(slots)} slots."}

        if action_obj.action == "block_room":
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("rooms", action_obj.room_id, slots, "block")
            if not ok: return {"status": "error", "message": err}
            name = ctx["rooms"].get(action_obj.room_id, "that room")
            return {"status": "success", "message": f"Closed **{name}** for {len(slots)} slots."}

        if action_obj.action == "unblock_room":
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("rooms", action_obj.room_id, slots, "unblock")
            if not ok: return {"status": "error", "message": err}
            name = ctx["rooms"].get(action_obj.room_id, "that room")
            return {"status": "success", "message": f"Opened **{name}** again."}

        if action_obj.action == "block_subject":
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("subjects", action_obj.subject_id, slots, "block")
            if not ok: return {"status": "error", "message": err}
            name = ctx["subjects"].get(action_obj.subject_id, "that subject")
            return {"status": "success", "message": f"Restricted **{name}** on {len(slots)} slots."}
        
        if action_obj.action == "unblock_subject":
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("subjects", action_obj.subject_id, slots, "unblock")
            if not ok: return {"status": "error", "message": err}
            name = ctx["subjects"].get(action_obj.subject_id, "that subject")
            return {"status": "success", "message": f"Restrictions removed for **{name}**."}

        if action_obj.action == "force_subject":
            if not action_obj.subject_id: return {"status": "error", "message": "Missing subject ID"}
            ref = db.collection("subjects").document(action_obj.subject_id)
            if ref.get().exists:
                ref.update({
                    "fixed_slot": action_obj.target_slot_id,
                    "unavailable_slots": [] 
                })
                s_name = ctx["subjects"].get(action_obj.subject_id, "Subject")
                return {"status": "success", "message": get_move_msg(s_name, action_obj.target_slot_id)}
            return {"status": "error", "message": "I couldn't find that subject!"}

        return {"status": "success", "message": "I've noted that constraint down."}

    def analyze_solver_failure(self, data, error):
        return "Solver failed. Try telling me to 'Clear all constraints' or 'Reset' to start over."