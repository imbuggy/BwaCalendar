import os
import imaplib
import email
from email.header import decode_header
import json
from datetime import datetime
from supabase import create_client, Client
import google.generativeai as genai
import io

# New dependencies for attachment parsing (User must install pypdf and python-docx)
try:
    from pypdf import PdfReader
    import docx
except ImportError:
    print("Warning: pypdf or python-docx not found. Attachment parsing will be restricted.")

# Environment Configuration
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASS = os.getenv('EMAIL_PASS')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Model & Client Setup
genai.configure(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SYSTEM_INSTRUCTIONS = """
Role: BWA/EdW School Intelligence Engine. 
Class & Stream Taxonomy (Strict Codes):
- N = Nursery (English)
- R = Reception English Stream
- Rb = Reception Bilingual Stream / MSB
- Y1 = Year 1 English Stream
- Y1b = Year 1 Bilingual Stream / GSB
- Y2 = Year 2 English Stream
- Y2b = Year 2 Bilingual Stream / CPB
- Y3 = Year 3 English Stream
- Y3b = Year 3 Bilingual Stream / CE1B
- Y4 = Year 4 English Stream
- Y4b = Year 4 Bilingual Stream / CE2B
- Y5 = Year 5 English Stream
- Y5b = Year 5 Bilingual Stream / CM1B
- Y6 = Year 6 English Stream
- Y6b = Year 6 Bilingual Stream / CM2B

Privacy: If PII (specific student names, health reports, behavior incidents) is detected, return {"status": "REJECTED"}.

Task: Extract all events from the email and its attachments. 
Forward Detection Rule:
- Look inside the raw body for forward headers (e.g., "From:", "Sent:", "Subject:", "Date:"). 
- Priority: Subject/Date/Time from the forward header.

Event Formatting Rules:
1. Dates: 
   - Identify 'single-day' or 'date-range' (e.g., '2026-04-20' to '2026-04-24').
   - For ranges, provide both 'event_date' (start) and 'event_date_end'.
   - Include 'formatted_date_display' which includes the day of the week (e.g., "Mon 20 Apr" or "Mon 20 - Fri 24 Apr").
2. Time: Identify if it is 'all-day', a single time (e.g. '09:00'), or a range.
3. Summary: Provide a 'summary' that is 1-5 sentences. If the content is complex, provide a longer 'full_details' string.
4. Sources: Include 'source_title', 'source_date', and 'source_time'.
5. Links: Extract URLs into a 'links' array.

Output: JSON array of events.
Each event object: {
  "action": "insert" | "update",
  "match_id": integer | null,
  "event_data": {
    "title": "string",
    "event_date": "YYYY-MM-DD",
    "event_date_end": "YYYY-MM-DD" | null,
    "formatted_date_display": "string",
    "time_type": "all-day" | "single" | "range",
    "time_value": "string",
    "classes": ["code1", "code2"],
    "summary": "string",
    "full_details": "string" | null,
    "source_title": "string",
    "source_date": "string",
    "source_time": "string",
    "links": [{"title": "string", "url": "string"}],
    "status": "approved"
  }
}
"""

def extract_pdf_text(payload):
    try:
        reader = PdfReader(io.BytesIO(payload))
        return " ".join([page.extract_text() for page in reader.pages])
    except Exception as e:
        return f"[PDF Error: {e}]"

def extract_docx_text(payload):
    try:
        doc = docx.Document(io.BytesIO(payload))
        return " ".join([para.text for para in doc.paragraphs])
    except Exception as e:
        return f"[DOCX Error: {e}]"

def fetch_emails():
    print("Connecting to IMAP...")
    mail = imaplib.IMAP4_SSL("imap.hostinger.com")
    mail.login(EMAIL_USER, EMAIL_PASS)
    mail.select("inbox")
    
    status, messages = mail.search(None, 'UNSEEN')
    email_ids = messages[0].split()
    
    email_data = []
    for e_id in email_ids:
        status, msg_data = mail.fetch(e_id, '(RFC822)')
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                subject = decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                
                date_str = msg['Date']
                
                body = ""
                attachments_text = ""
                
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))
                        
                        if "attachment" in content_disposition:
                            filename = part.get_filename()
                            if filename:
                                payload = part.get_payload(decode=True)
                                if filename.lower().endswith(".pdf"):
                                    attachments_text += f"\n[ATTACHMENT: {filename}]\n" + extract_pdf_text(payload)
                                elif filename.lower().endswith(".docx"):
                                    attachments_text += f"\n[ATTACHMENT: {filename}]\n" + extract_docx_text(payload)
                        elif content_type == "text/plain":
                            body = part.get_payload(decode=True).decode()
                else:
                    body = msg.get_payload(decode=True).decode()
                
                # Filter: Only keep emails from schoolcomms.com, Belleville Wix Academy, or Wix admin
                from_header_str = msg.get('From', '').lower()
                reply_to_str = msg.get('Reply-To', '').lower()
                content_lower = (subject + " " + body + " " + attachments_text).lower()
                
                # Obfuscated trusted emails to prevent scraping
                admin_id_1 = "".join([chr(x) for x in [97, 100, 109, 105, 110, 64, 119, 105, 120, 46, 119, 97, 110, 100, 115, 119, 111, 114, 116, 104, 46, 115, 99, 104, 46, 117, 107]])
                admin_id_2 = "".join([chr(x) for x in [97, 100, 109, 105, 110, 64, 98, 101, 108, 108, 101, 118, 105, 108, 108, 101, 119, 105, 120, 46, 117, 107, 46, 113, 49, 101, 46, 111, 114, 103, 46, 117, 107]])
                trusted_identifiers = ['schoolcomms.com', 'belleville wix academy', admin_id_1, admin_id_2]
                
                is_trusted = any(id in from_header_str or id in reply_to_str for id in trusted_identifiers)
                is_forwarded_from_trusted = any(id in content_lower for id in trusted_identifiers)
                
                if not (is_trusted or is_forwarded_from_trusted):
                    print(f"Skipping irrelevant email: {subject}")
                    continue

                email_data.append({
                    "subject": subject,
                    "from": from_header_str,
                    "date": date_str,
                    "content": body + "\n" + attachments_text
                })
    mail.logout()
    return email_data

def get_existing_events():
    response = supabase.table("events").select("*").execute()
    return response.data

def process_data(email_items, existing_db):
    model = genai.GenerativeModel('gemini-3-flash-preview', system_instruction=SYSTEM_INSTRUCTIONS)
    processed_results = []
    
    for item in email_items:
        prompt = (
            f"Existing Database: {json.dumps(existing_db)}\n"
            f"Source Email Title: {item['subject']}\n"
            f"Source Email Date: {item['date']}\n"
            f"Email & Attachment Content: {item['content']}"
        )
        response = model.generate_content(prompt)
        try:
            text = response.text.strip().replace('```json', '').replace('```', '')
            results = json.loads(text)
            if isinstance(results, list):
                processed_results.extend(results)
            else:
                processed_results.append(results)
        except Exception as e:
            print(f"Error parsing Gemini response: {e}")
            
    return processed_results

def sync_database(results):
    for item in results:
        if item.get('status') == 'REJECTED':
            print("PII Detected. Record rejected.")
            continue
            
        action = item.get('action')
        data = item.get('event_data')
        
        if not data: continue

        if action == 'insert':
            supabase.table("events").insert(data).execute()
        elif action == 'update' and item.get('match_id'):
            supabase.table("events").update(data).eq("id", item.get('match_id')).execute()

if __name__ == "__main__":
    if not all([EMAIL_USER, EMAIL_PASS, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
        print("Missing environment variables.")
        exit(1)
        
    email_data = fetch_emails()
    if email_data:
        db_state = get_existing_events()
        results = process_data(email_data, db_state)
        sync_database(results)
        # generate_ics_file() # Disabled for now
    else:
        print("No new emails found.")
