import firebase_admin
from firebase_admin import credentials, firestore

CRED_PATH = "firebase_credentials.json"

if not firebase_admin._apps:
    cred = credentials.Certificate(CRED_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()

def delete_collection(coll_name, batch_size):
    coll_ref = db.collection(coll_name)
    docs = coll_ref.limit(batch_size).stream()
    deleted = 0
    for doc in docs:
        doc.reference.delete()
        deleted += 1
    if deleted >= batch_size:
        delete_collection(coll_name, batch_size)

def seed_database():
    print("⚠️ Clearing old data...")
    collections = ["teachers", "subjects", "rooms", "academic_years", "assigned_classes", "generated_schedule", "system_config"]
    for c in collections:
        delete_collection(c, 50)

    batch = db.batch()

    # --- 1. SYSTEM CONFIG ---
    print("⚙️ Seeding Config...")
    batch.set(db.collection("system_config").document("main_settings"), {
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        "periods_per_day": 8,
        "start_time": "08:00",
        "period_duration": 50,
        "break_after_period": 4,
        "break_duration": 30,
        "max_block_duration": 3
    })

    # --- 2. MASTER: TEACHERS (Manual IDs) ---
    print("👨‍🏫 Seeding Teachers...")
    teachers = [
        {"id": "TCH-001", "name": "Dr. Alan (CS)", "unavailable_slots": ["Mon_1", "Mon_2"]},
        {"id": "TCH-002", "name": "Prof. Ada (Math)", "unavailable_slots": []},
        {"id": "TCH-003", "name": "Mr. Newton (Phys)", "unavailable_slots": ["Fri_7", "Fri_8"]},
        {"id": "TCH-004", "name": "Ms. Austen (Eng)", "unavailable_slots": ["Wed_1", "Wed_2"]},
        {"id": "TCH-005", "name": "Mr. Herodotus (Hist)", "unavailable_slots": []},
        {"id": "TCH-006", "name": "Dr. Darwin (Bio)", "unavailable_slots": ["Tue_1"]},
    ]
    for t in teachers:
        batch.set(db.collection("teachers").document(t["id"]), t)

    # --- 3. MASTER: ROOMS (Manual IDs) ---
    print("🏫 Seeding Rooms...")
    rooms = [
        {"id": "LAB-A", "name": "Physics Lab", "capacity": 30},
        {"id": "LAB-B", "name": "Bio/Chem Lab", "capacity": 30},
        {"id": "RM-101", "name": "Lecture Hall A", "capacity": 50},
        {"id": "RM-102", "name": "Classroom 102", "capacity": 30},
        {"id": "RM-103", "name": "Classroom 103", "capacity": 30},
        {"id": "GYM",    "name": "Gymnasium", "capacity": 100},
    ]
    for r in rooms:
        batch.set(db.collection("rooms").document(r["id"]), r)

    # --- 4. MASTER: SUBJECTS (Auto IDs) ---
    print("📚 Seeding Subjects...")
    # We need to capture the IDs to use them in assignments
    subs = {}
    subject_list = [
        {"name": "Mathematics", "sks": 4},
        {"name": "Physics", "sks": 4},
        {"name": "Computer Science", "sks": 3},
        {"name": "English Lit", "sks": 2},
        {"name": "History", "sks": 2},
        {"name": "Biology", "sks": 3},
        {"name": "Physical Ed", "sks": 2},
        {"name": "Adv Workshop", "sks": 6}, # High SKS
    ]
    
    for s in subject_list:
        ref = db.collection("subjects").document()
        # Clean key for local lookup (e.g., "Computer Science" -> "computer_science")
        key = s["name"].lower().replace(" ", "_")
        subs[key] = ref.id
        batch.set(ref, {"id": ref.id, "name": s["name"], "sks": s["sks"]})

    # --- 5. MASTER: ACADEMIC YEARS (Auto IDs) ---
    print("🎓 Seeding Years...")
    years = {}
    year_list = ["Grade 10", "Grade 11", "Grade 12"]
    
    for y_name in year_list:
        ref = db.collection("academic_years").document()
        key = y_name.lower().replace(" ", "_")
        years[key] = ref.id
        batch.set(ref, {"id": ref.id, "name": y_name})

    # --- 6. ASSIGNED CLASSES (The Engine Inputs) ---
    print("🔗 Seeding Assignments (The Contracts)...")
    
    # Helper to create assignments easily
    assignments_data = []

    # --- Grade 10 Curriculum ---
    assignments_data.extend([
        # Math with Ada (4 SKS)
        {"t": "TCH-002", "s": subs["mathematics"], "y": years["grade_10"], "sks": 4},
        # Physics with Newton (3 SKS)
        {"t": "TCH-003", "s": subs["physics"], "y": years["grade_10"], "sks": 3},
        # English with Austen (2 SKS)
        {"t": "TCH-004", "s": subs["english_lit"], "y": years["grade_10"], "sks": 2},
        # PE in Gym (2 SKS) - Teacher TCH-006 covers this for now
        {"t": "TCH-006", "s": subs["physical_ed"], "y": years["grade_10"], "sks": 2},
    ])

    # --- Grade 11 Curriculum ---
    assignments_data.extend([
        # Math with Ada (4 SKS) - Potential Conflict with Gr 10 if not handled
        {"t": "TCH-002", "s": subs["mathematics"], "y": years["grade_11"], "sks": 4},
        # CS with Alan (3 SKS)
        {"t": "TCH-001", "s": subs["computer_science"], "y": years["grade_11"], "sks": 3},
        # Bio with Darwin (3 SKS)
        {"t": "TCH-006", "s": subs["biology"], "y": years["grade_11"], "sks": 3},
        # History with Herodotus (2 SKS)
        {"t": "TCH-005", "s": subs["history"], "y": years["grade_11"], "sks": 2},
    ])

    # --- Grade 12 Curriculum ---
    assignments_data.extend([
        # Adv Workshop (6 SKS) -> Should split into 3+3 or 3+2+1 depending on logic
        {"t": "TCH-003", "s": subs["adv_workshop"], "y": years["grade_12"], "sks": 6},
        # CS Project with Alan (4 SKS)
        {"t": "TCH-001", "s": subs["computer_science"], "y": years["grade_12"], "sks": 4},
        # English with Austen (2 SKS)
        {"t": "TCH-004", "s": subs["english_lit"], "y": years["grade_12"], "sks": 2},
    ])

    # Batch set assignments
    for i, a in enumerate(assignments_data):
        doc_ref = db.collection("assigned_classes").document()
        batch.set(doc_ref, {
            "id": doc_ref.id,
            "teacher_id": a["t"],
            "subject_id": a["s"],
            "year_id": a["y"],
            "sks": a["sks"]
        })

    batch.commit()
    print(f"🚀 Database seeded with {len(assignments_data)} Assigned Classes across 3 Grades!")

if __name__ == "__main__":
    seed_database()