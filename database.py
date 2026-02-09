import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
load_dotenv()

# Initialize Firebase app only once
if not firebase_admin._apps:
    firebase_creds = os.getenv("FIREBASE_CREDENTIALS")

    if not firebase_creds:
        raise RuntimeError(
            "FIREBASE_CREDENTIALS env variable is not set"
        )

    cred = credentials.Certificate(
        json.loads(firebase_creds)
    )

    firebase_admin.initialize_app(cred)

def get_db():
    return firestore.client()

db = get_db()

def fetch_all_data():
    """
    Fetches the Relational Data Structure.
    """
    data = {
        "config": {},
        "teachers": [],
        "rooms": [],
        "subjects": [],
        "academic_years": [],
        "assigned_classes": [], # The Engine Inputs
        "generated_schedule": []
    }

    try:
        # 1. Fetch Singleton Config
        config_doc = db.collection("system_config").document("main_settings").get()
        if config_doc.exists:
            data["config"] = config_doc.to_dict()
        else:
            # Fallback defaults
            data["config"] = {
                "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "periods_per_day": 8,
                "period_duration": 50
            }

        # 2. Fetch Master Collections
        # We assume the lists are small enough to fetch all at once for the solver
        data["teachers"] = [d.to_dict() for d in db.collection("teachers").stream()]
        data["rooms"] = [d.to_dict() for d in db.collection("rooms").stream()]
        data["subjects"] = [d.to_dict() for d in db.collection("subjects").stream()]
        data["academic_years"] = [d.to_dict() for d in db.collection("academic_years").stream()]
        
        # 3. Fetch The Contracts (Assignments)
        data["assigned_classes"] = [d.to_dict() for d in db.collection("assigned_classes").stream()]
            
    except Exception as e:
        print(f"Error fetching data: {e}")
        return {}

    return data

def save_schedule(assignments):
    """
    Wipes old schedule and saves new results.
    """
    batch = db.batch()
    
    # 1. Delete old (Limit 500 for batch safety)
    old_docs = db.collection("generated_schedule").limit(500).stream()
    for doc in old_docs:
        doc.reference.delete()

    # 2. Save new
    print(f"💾 Saving {len(assignments)} assignments to Firestore...")
    for item in assignments:
        # Auto-generated ID logic (or custom if preferred)
        # We let Firestore generate the ID, or create a unique composite key
        doc_ref = db.collection("generated_schedule").document() 
        batch.set(doc_ref, item)

    batch.commit()
    print("✅ Schedule saved.")