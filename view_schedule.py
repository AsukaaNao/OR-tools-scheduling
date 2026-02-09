import firebase_admin
import json
import os
import dotenv
dotenv.load_dotenv()

from firebase_admin import credentials, firestore

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

db = firestore.client()

def view_schedule():
    print("\n🔥 Reading Schedule from 'generated_schedule'...\n")
    
    docs = db.collection("generated_schedule").stream()
    schedule = [d.to_dict() for d in docs]
    
    if not schedule:
        print("No schedule found.")
        return

    # Sort keys: Day Index, Period Index, Year Name
    day_map = {"Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5}
    
    schedule.sort(key=lambda x: (
        day_map.get(x['slot_id'].split('_')[0], 99), 
        int(x['slot_id'].split('_')[1]),
        x.get('year', '')
    ))

    print(f"{'DAY':<5} | {'SLOT':<6} | {'DUR':<3} | {'COHORT':<10} | {'SUBJECT':<20} | {'TEACHER':<10} | {'ROOM':<10}")
    print("-" * 100)

    for item in schedule:
        slot = item['slot_id']
        day, period = slot.split('_')
        dur = item.get('duration', 1)
        subj = item.get('subject_name', 'Unknown')
        teach = item.get('teacher_id', 'Unknown')
        room = item.get('room_id', 'Unknown')
        year = item.get('year', 'N/A')

        print(f"{day:<5} | P-{period:<4} | {dur:<3} | {year:<10} | {subj:<20} | {teach:<10} | {room:<10}")

if __name__ == "__main__":
    view_schedule()