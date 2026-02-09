from ortools.sat.python import cp_model
import random

class SchoolScheduler:
    def __init__(self, data):
        self.data = data
        self.blocks = data.get("blocks", [])
        
        self.teachers = {t["id"]: t for t in data["teachers"]}
        self.rooms = {r["id"]: r for r in data["rooms"]}
        self.subjects = {s["id"]: s for s in data["subjects"]}
        
        self.config = data["config"]
        self.days = self.config.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri"])
        self.periods = self.config.get("periods_per_day", 8)
        
        self.slots = []
        for d in self.days:
            for p in range(1, self.periods + 1):
                self.slots.append({
                    "id": f"{d}_{p}",
                    "day": d,
                    "period": p
                })

    def solve(self, randomize: bool = False):
        model = cp_model.CpModel()
        assignments = [] 

        # --- PRE-CALC VALID START SLOTS ---
        valid_starts = {} 
        for b in self.blocks:
            duration = b["duration"]
            valid = []
            
            s_id = b.get("subject_id")
            subject_obj = self.subjects.get(s_id)
            fixed_slot = subject_obj.get("fixed_slot") if subject_obj else None

            if fixed_slot:
                valid = [fixed_slot]
            else:
                for s in self.slots:
                    # Boundary check (Class must finish within the day)
                    if s["period"] + duration - 1 <= self.periods:
                        valid.append(s["id"])
            
            valid_starts[b["block_id"]] = valid

        # --- VARIABLES & DETAILED ERROR CHECKING ---
        x = {}

        for b in self.blocks:
            b_id = b["block_id"]
            t_id = b["teacher_id"]
            s_id = b.get("subject_id")
            s_name = b["subject_name"]
            
            # 1. Room Check
            allowed_rooms = [r["id"] for r in self.rooms.values()] 
            if not allowed_rooms: return {"status": "failure", "error": "No rooms defined in database."}

            possible_slots_count = 0

            for r_id in allowed_rooms:
                for start_s_id in valid_starts[b_id]:
                    
                    # AVAILABILITY LOGIC
                    is_available = True
                    
                    try:
                        start_slot = next(sl for sl in self.slots if sl["id"] == start_s_id)
                    except StopIteration:
                        # Fixed slot was invalid (e.g. "Sat_1")
                        continue 
                        
                    occupied_slots = [
                        f"{start_slot['day']}_{start_slot['period'] + k}" 
                        for k in range(b["duration"])
                    ]

                    # 1. Check Teacher
                    teacher_obj = self.teachers.get(t_id)
                    if teacher_obj:
                        unavailable = teacher_obj.get("unavailable_slots", [])
                        if any(os in unavailable for os in occupied_slots):
                            is_available = False

                    # 2. Check Subject (unless Fixed)
                    subject_obj = self.subjects.get(s_id)
                    if subject_obj and not subject_obj.get("fixed_slot"):
                        unavailable = subject_obj.get("unavailable_slots", [])
                        if any(os in unavailable for os in occupied_slots):
                            is_available = False

                    if is_available:
                        x[(b_id, r_id, start_s_id)] = model.NewBoolVar(f"{b_id}_{r_id}_{start_s_id}")
                        possible_slots_count += 1

            # --- CRITICAL ERROR CATCHER ---
            # If after checking ALL rooms and ALL slots, this specific block has 0 options:
            if possible_slots_count == 0:
                # Let's figure out WHY to tell the user
                subject_obj = self.subjects.get(s_id)
                teacher_obj = self.teachers.get(t_id)
                
                error_msg = f"IMPOSSIBLE REQUEST: '{s_name}' ({b['duration']} hrs) has 0 valid slots."
                
                if subject_obj and subject_obj.get("fixed_slot"):
                    error_msg += f" It is FORCED to {subject_obj.get('fixed_slot')}, but the Teacher/Room is blocked there."
                else:
                    error_msg += f" Teacher {teacher_obj.get('name')} might be blocked too heavily, or no chunk of {b['duration']} hours is free."
                
                return {"status": "failure", "error": error_msg}

        # --- CONSTRAINTS (Standard) ---
        for b in self.blocks:
            b_id = b["block_id"]
            vars_for_block = [v for (bid, rid, sid), v in x.items() if bid == b_id]
            model.Add(sum(vars_for_block) == 1)

        # Overlap Logic
        room_usage = {r["id"]: {s["id"]: [] for s in self.slots} for r in self.rooms.values()}
        teacher_usage = {t["id"]: {s["id"]: [] for s in self.slots} for t in self.teachers.values()}
        year_usage = {} 

        for (b_id, r_id, s_id), var in x.items():
            b = next(blk for blk in self.blocks if blk["block_id"] == b_id)
            duration = b["duration"]
            t_id = b["teacher_id"]
            y_id = b["year_id"]

            if y_id not in year_usage: year_usage[y_id] = {s["id"]: [] for s in self.slots}

            start_slot = next(sl for sl in self.slots if sl["id"] == s_id)
            occupied_slots = [
                f"{start_slot['day']}_{start_slot['period'] + k}" 
                for k in range(duration)
            ]

            for occ in occupied_slots:
                if occ in room_usage[r_id]: room_usage[r_id][occ].append(var)
                if t_id in teacher_usage and occ in teacher_usage[t_id]: teacher_usage[t_id][occ].append(var)
                if y_id in year_usage and occ in year_usage[y_id]: year_usage[y_id][occ].append(var)

        for r_id in room_usage:
            for s_id in room_usage[r_id]: model.Add(sum(room_usage[r_id][s_id]) <= 1)
        for t_id in teacher_usage:
            for s_id in teacher_usage[t_id]: model.Add(sum(teacher_usage[t_id][s_id]) <= 1)
        for y_id in year_usage:
            for s_id in year_usage[y_id]: model.Add(sum(year_usage[y_id][s_id]) <= 1)

        # --- SOLVE ---
        solver = cp_model.CpSolver()
        if randomize:
            solver.parameters.random_seed = random.randint(1, 10000)
            solver.parameters.num_search_workers = 8

        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for (b_id, r_id, s_id), var in x.items():
                if solver.Value(var) == 1:
                    b = next(blk for blk in self.blocks if blk["block_id"] == b_id)
                    assignments.append({
                        "slot_id": s_id,
                        "duration": b["duration"],
                        "subject_name": b["subject_name"],
                        "teacher_id": b["teacher_id"],
                        "room_id": r_id,
                        "year": b["year_name"],
                        "block_id": b_id
                    })
            return {"status": "success", "data": assignments}
        
        return {"status": "failure", "error": "Mathematical Conflict: Too many overlapping classes (Teachers/Rooms/Years) at the same time."}