import os
from typing import Annotated, List, Literal, Union, Dict, Any
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from database import db, fetch_all_data
from firebase_admin import firestore # Import for DELETE_FIELD
import random
from dotenv import load_dotenv
load_dotenv()

# ---------------- CONFIG ----------------

API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-3-flash-preview" 

# --- SCHEMAS ---

# 1. BLOCKING (Negative Constraints)
class BlockTeacher(BaseModel):
    action: Literal["block_teacher"]
    teacher_id: str
    slot_ids: List[str]

class BlockRoom(BaseModel):
    action: Literal["block_room"]
    room_id: str
    slot_ids: List[str]

class BlockSubject(BaseModel):
    action: Literal["block_subject"]
    subject_id: str
    slot_ids: List[str]

# 2. UNBLOCKING (Removing Constraints)
class UnblockSubject(BaseModel):
    action: Literal["unblock_subject"]
    subject_id: str
    slot_ids: List[str]

class UnblockTeacher(BaseModel):
    action: Literal["unblock_teacher"]
    teacher_id: str
    slot_ids: List[str]

class UnblockRoom(BaseModel):
    action: Literal["unblock_room"]
    room_id: str
    slot_ids: List[str]

# 3. FORCING (Positive Move Constraints)
class ForceSubject(BaseModel):
    action: Literal["force_subject"]
    subject_id: str
    target_slot_id: str = Field(description="The single specific start slot, e.g. 'Mon_1'")

# 4. RESET (The Nuclear Option)
class ClearAllConstraints(BaseModel):
    action: Literal["clear_all_constraints"]
    confirmation: bool = Field(description="Always true if user asks to reset everything.")

class GeneralConstraint(BaseModel):
    action: Literal["general_constraint"]
    description: str




AgentResponse = Annotated[
    Union[
        BlockTeacher,
        BlockRoom,
        BlockSubject,
        UnblockTeacher,
        UnblockRoom,
        UnblockSubject,
        ForceSubject,
        ClearAllConstraints,
        GeneralConstraint,
    ],
    Field(discriminator="action"),
]

class AgentAction(BaseModel):
    response: AgentResponse

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
        1. **BLOCK**: "busy", "unavailable", "can't", "don't put" -> 'block_*'
        2. **UNBLOCK**: "free", "available", "remove restriction" -> 'unblock_*'
        3. **MOVE/FORCE**: "Move [Subject] to [Day] [Slot]", "Must start at..." -> 'force_subject'.
        4. **RESET**: "Remove all constraints", "Clear everything", "Reset", "Start fresh" -> 'clear_all_constraints'.
        
        USER COMMAND: "{user_text}"
        """
        
        try:
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AgentResponse
                )
            )
            parsed_action = response.parsed.response
            return self.execute_action(parsed_action, ctx)
            
        except Exception as e:
            return {"status": "error", "message": f"I got confused! Error: {str(e)}"}

    def expand_slots(self, slot_ids: List[str]) -> List[str]:
        final_slots = []
        for s in slot_ids:
            if len(s) == 3 and "_" not in s: # e.g. "Mon"
                final_slots.extend([f"{s}_{i}" for i in range(1, 9)])
            else:
                final_slots.append(s)
        return list(set(final_slots))

    def execute_action(self, action_obj, ctx: Dict):
        
        # --- HELPER: UPDATER ---
        def update_constraint(collection, doc_id, slots, mode="block"):
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
            """
            Iterates through a collection and clears specific fields.
            """
            batch = db.batch()
            count = 0
            docs = db.collection(collection_name).stream()
            
            for doc in docs:
                batch.update(doc.reference, fields_to_reset)
                count += 1
                if count >= 400: # Commit in chunks to avoid limits
                    batch.commit()
                    batch = db.batch()
                    count = 0
            
            if count > 0:
                batch.commit()

        # --- RESPONSE MESSAGES ---
        def get_reset_msg():
            msgs = [
                "Tabula Rasa! I've wiped all constraints. We are starting fresh.",
                "Done. I've cleared every restriction for Teachers, Rooms, and Subjects.",
                "Reset complete! The schedule is now a blank canvas with no rules.",
                "Boom! 💥 All constraints deleted. Let's try generating again.",
            ]
            return random.choice(msgs)

        def get_move_msg(name, slot):
            msgs = [
                f"Aye aye! I've pinned **{name}** to start exactly at **{slot}**.",
                f"Moved! **{name}** is locked to start at **{slot}**.",
                f"You're the boss. **{name}** will happen at **{slot}**.",
            ]
            return random.choice(msgs)

        # --- HANDLERS ---

        # 1. CLEAR ALL (NUCLEAR OPTION)
        if isinstance(action_obj, ClearAllConstraints):
            try:
                # Reset Teachers
                wipe_collection_constraints("teachers", {"unavailable_slots": []})
                # Reset Rooms
                wipe_collection_constraints("rooms", {"unavailable_slots": []})
                # Reset Subjects (Clear blocks AND fixed slots)
                wipe_collection_constraints("subjects", {
                    "unavailable_slots": [], 
                    "fixed_slot": firestore.DELETE_FIELD
                })
                return {"status": "success", "message": get_reset_msg()}
            except Exception as e:
                return {"status": "error", "message": f"Failed to reset: {str(e)}"}

        # 2. TEACHERS
        if isinstance(action_obj, BlockTeacher):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("teachers", action_obj.teacher_id, slots, "block")
            if not ok: return {"status": "error", "message": err}
            name = ctx["teachers"].get(action_obj.teacher_id, "that teacher")
            return {"status": "success", "message": f"Blocked **{name}** for {len(slots)} slots."}

        if isinstance(action_obj, UnblockTeacher):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("teachers", action_obj.teacher_id, slots, "unblock")
            if not ok: return {"status": "error", "message": err}
            name = ctx["teachers"].get(action_obj.teacher_id, "that teacher")
            return {"status": "success", "message": f"Freed up **{name}** on {len(slots)} slots."}

        # 3. ROOMS
        if isinstance(action_obj, BlockRoom):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("rooms", action_obj.room_id, slots, "block")
            if not ok: return {"status": "error", "message": err}
            name = ctx["rooms"].get(action_obj.room_id, "that room")
            return {"status": "success", "message": f"Closed **{name}** for {len(slots)} slots."}

        if isinstance(action_obj, UnblockRoom):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("rooms", action_obj.room_id, slots, "unblock")
            if not ok: return {"status": "error", "message": err}
            name = ctx["rooms"].get(action_obj.room_id, "that room")
            return {"status": "success", "message": f"Opened **{name}** again."}

        # 4. SUBJECTS (BLOCK/UNBLOCK)
        if isinstance(action_obj, BlockSubject):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("subjects", action_obj.subject_id, slots, "block")
            if not ok: return {"status": "error", "message": err}
            name = ctx["subjects"].get(action_obj.subject_id, "that subject")
            return {"status": "success", "message": f"Restricted **{name}** on {len(slots)} slots."}
        
        if isinstance(action_obj, UnblockSubject):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint("subjects", action_obj.subject_id, slots, "unblock")
            if not ok: return {"status": "error", "message": err}
            name = ctx["subjects"].get(action_obj.subject_id, "that subject")
            return {"status": "success", "message": f"Restrictions removed for **{name}**."}

        # 5. FORCE / MOVE (SUBJECT)
        if isinstance(action_obj, ForceSubject):
            ref = db.collection("subjects").document(action_obj.subject_id)
            if ref.get().exists:
                ref.update({
                    "fixed_slot": action_obj.target_slot_id,
                    "unavailable_slots": [] # Clear conflicts if forcing
                })
                s_name = ctx["subjects"].get(action_obj.subject_id, "Subject")
                return {"status": "success", "message": get_move_msg(s_name, action_obj.target_slot_id)}
            return {"status": "error", "message": "I couldn't find that subject!"}

        # --- DEFAULT ---
        return {"status": "success", "message": "I've noted that constraint down."}

    def analyze_solver_failure(self, data, error):
        return "Solver failed. Try telling me to 'Clear all constraints' or 'Reset' to start over."