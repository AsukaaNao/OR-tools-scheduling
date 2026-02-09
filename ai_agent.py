import os
import random
from typing import List, Literal, Union, Dict
from dotenv import load_dotenv

from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from firebase_admin import firestore

from database import db, fetch_all_data

load_dotenv()

# ================= CONFIG =================

API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-3-flash-preview"

# ================= SCHEMAS =================

# --- BLOCKING ---
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

# --- UNBLOCKING ---
class UnblockTeacher(BaseModel):
    action: Literal["unblock_teacher"]
    teacher_id: str
    slot_ids: List[str]

class UnblockRoom(BaseModel):
    action: Literal["unblock_room"]
    room_id: str
    slot_ids: List[str]

class UnblockSubject(BaseModel):
    action: Literal["unblock_subject"]
    subject_id: str
    slot_ids: List[str]

# --- FORCE ---
class ForceSubject(BaseModel):
    action: Literal["force_subject"]
    subject_id: str
    target_slot_id: str = Field(
        description="Single start slot like 'Mon_1'"
    )

# --- RESET ---
class ClearAllConstraints(BaseModel):
    action: Literal["clear_all_constraints"]
    confirmation: bool = Field(
        description="Must be true if user asks to reset"
    )

# --- FALLBACK ---
class GeneralConstraint(BaseModel):
    action: Literal["general_constraint"]
    description: str

# --- ROOT AGENT RESPONSE ---
class AgentAction(BaseModel):
    response: Union[
        BlockTeacher,
        BlockRoom,
        BlockSubject,
        UnblockTeacher,
        UnblockRoom,
        UnblockSubject,
        ForceSubject,
        ClearAllConstraints,
        GeneralConstraint,
    ]

# ================= AGENT =================

class AIAgent:

    # ---------- CONTEXT ----------
    def get_context(self) -> Dict:
        data = fetch_all_data()
        return {
            "teachers": {t["id"]: t["name"] for t in data.get("teachers", [])},
            "rooms": {r["id"]: r["name"] for r in data.get("rooms", [])},
            "subjects": {s["id"]: s["name"] for s in data.get("subjects", [])},
        }

    # ---------- MAIN ENTRY ----------
    def process_command(self, user_text: str) -> dict:
        ctx = self.get_context()

        prompt = f"""
You are a School Scheduler Assistant.

CONTEXT:
Subjects: {ctx['subjects']}
Teachers: {ctx['teachers']}
Rooms: {ctx['rooms']}

INSTRUCTIONS:
1. BLOCK → block_*
2. UNBLOCK → unblock_*
3. FORCE → force_subject
4. RESET → clear_all_constraints

USER COMMAND:
"{user_text}"
"""

        try:
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AgentAction,
                ),
            )

            # ================= CRITICAL FIX =================
            parsed = response.parsed

            # Gemini MAY return a dict in production → revalidate
            if isinstance(parsed, dict):
                parsed = AgentAction.model_validate(parsed)

            action_obj = parsed.response
            # =================================================

            return self.execute_action(action_obj, ctx)

        except Exception as e:
            return {
                "status": "error",
                "message": f"I got confused! Error: {str(e)}",
            }

    # ---------- SLOT EXPANDER ----------
    def expand_slots(self, slot_ids: List[str]) -> List[str]:
        final = []
        for s in slot_ids:
            if len(s) == 3 and "_" not in s:
                final.extend([f"{s}_{i}" for i in range(1, 9)])
            else:
                final.append(s)
        return list(set(final))

    # ---------- EXECUTION ----------
    def execute_action(self, action_obj, ctx: Dict):

        # ----- helpers -----
        def update_constraint(collection, doc_id, slots, mode="block"):
            ref = db.collection(collection).document(doc_id)
            doc = ref.get()
            if not doc.exists:
                return False, f"ID {doc_id} not found."

            current = doc.to_dict().get("unavailable_slots", [])
            if mode == "block":
                updated = list(set(current + slots))
            else:
                updated = [s for s in current if s not in slots]

            ref.update({"unavailable_slots": updated})
            return True, None

        def wipe_collection(collection, fields):
            batch = db.batch()
            count = 0
            for doc in db.collection(collection).stream():
                batch.update(doc.reference, fields)
                count += 1
                if count >= 400:
                    batch.commit()
                    batch = db.batch()
                    count = 0
            if count:
                batch.commit()

        # ----- RESET -----
        if isinstance(action_obj, ClearAllConstraints):
            wipe_collection("teachers", {"unavailable_slots": []})
            wipe_collection("rooms", {"unavailable_slots": []})
            wipe_collection(
                "subjects",
                {
                    "unavailable_slots": [],
                    "fixed_slot": firestore.DELETE_FIELD,
                },
            )
            return {
                "status": "success",
                "message": "All constraints cleared. Fresh start ✨",
            }

        # ----- TEACHERS -----
        if isinstance(action_obj, BlockTeacher):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint(
                "teachers", action_obj.teacher_id, slots, "block"
            )
            if not ok:
                return {"status": "error", "message": err}
            return {"status": "success", "message": "Teacher blocked."}

        if isinstance(action_obj, UnblockTeacher):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint(
                "teachers", action_obj.teacher_id, slots, "unblock"
            )
            if not ok:
                return {"status": "error", "message": err}
            return {"status": "success", "message": "Teacher unblocked."}

        # ----- ROOMS -----
        if isinstance(action_obj, BlockRoom):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint(
                "rooms", action_obj.room_id, slots, "block"
            )
            if not ok:
                return {"status": "error", "message": err}
            return {"status": "success", "message": "Room blocked."}

        if isinstance(action_obj, UnblockRoom):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint(
                "rooms", action_obj.room_id, slots, "unblock"
            )
            if not ok:
                return {"status": "error", "message": err}
            return {"status": "success", "message": "Room unblocked."}

        # ----- SUBJECTS -----
        if isinstance(action_obj, BlockSubject):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint(
                "subjects", action_obj.subject_id, slots, "block"
            )
            if not ok:
                return {"status": "error", "message": err}
            return {"status": "success", "message": "Subject restricted."}

        if isinstance(action_obj, UnblockSubject):
            slots = self.expand_slots(action_obj.slot_ids)
            ok, err = update_constraint(
                "subjects", action_obj.subject_id, slots, "unblock"
            )
            if not ok:
                return {"status": "error", "message": err}
            return {"status": "success", "message": "Subject freed."}

        # ----- FORCE -----
        if isinstance(action_obj, ForceSubject):
            ref = db.collection("subjects").document(action_obj.subject_id)
            if not ref.get().exists:
                return {"status": "error", "message": "Subject not found."}

            ref.update(
                {
                    "fixed_slot": action_obj.target_slot_id,
                    "unavailable_slots": [],
                }
            )
            return {
                "status": "success",
                "message": f"Subject forced to {action_obj.target_slot_id}",
            }

        # ----- FALLBACK -----
        return {
            "status": "success",
            "message": "Constraint noted.",
        }

    # ---------- SOLVER FAILURE ----------
    def analyze_solver_failure(self, data, error):
        return "Solver failed. Try clearing constraints."
