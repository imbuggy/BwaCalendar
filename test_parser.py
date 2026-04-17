import os
import json
import google.generativeai as genai
from main import SYSTEM_INSTRUCTIONS

# Setup
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

test_email = {
    "subject": "Upcoming Music Performances - BWA",
    "date": "Fri, 17 Apr 2026 12:58:00 +0000",
    "content": """Dear Parents and Carers,

We are delighted to share the plans for our upcoming music performances. The pupils have been working extremely hard in their wonderful music lessons, led by Mr Edwards. Year 5 pupils will perform at Fairfield Halls on 24th March. More details to follow on this later.  

Please see the performance dates for all other year groups below: 

Monday 16th March @ 2:30pm - Y1&2
Tuesday 17th March @ 2:15pm - Y3&4
Monday 23rd March @ 2:15pm - Y6
Tuesday 24th March - Y5 at Fairfield Halls


Please arrive outside the school office 5 minuites before the performance. Performances will take place in our middle hall. Pupils will return to class with their teacher and will be dismissed at their usual time on the playground.

Kind regards

Ellie Wilkes 
KS1 Phase lead and Class teacher 

Belleville Wix Academy
Wix's Lane, Clapham Common North Side, Clapham SW4 0AJ
t: 0207 228 3055
e: admin@bellevillewix.q1e.org.uk
"""
}

def test_parse():
    model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=SYSTEM_INSTRUCTIONS)
    prompt = (
        f"Existing Database: []\n"
        f"Source Email Title: {test_email['subject']}\n"
        f"Source Email Date: {test_email['date']}\n"
        f"Email & Attachment Content: {test_email['content']}"
    )
    
    print("Sending to Gemini for parsing...")
    response = model.generate_content(prompt)
    print("\n--- RAW GEMINI OUTPUT ---")
    print(response.text)
    print("------------------------\n")

if __name__ == "__main__":
    test_parse()
