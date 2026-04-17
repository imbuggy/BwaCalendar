import os
import imaplib
import email
from email.header import decode_header
import json
from datetime import datetime
from supabase import create_client, Client
from google import genai
from google.genai import types
import time
import io
import requests
from bs4 import BeautifulSoup

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
client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def safe_generate_content(contents, system_instruction=None):
    """Wrapper for Gemini API with retry logic for 429 rate limits."""
    max_retries = 3
    base_wait = 10
    
    for attempt in range(max_retries):
        try:
            # We add a small proactive sleep to stay under free tier RPM limits
            if attempt > 0:
                print(f"Retrying Gemini call (Attempt {attempt + 1}). Wait time: {base_wait * attempt}s")
                time.sleep(base_wait * attempt)
            
            config = types.GenerateContentConfig(system_instruction=system_instruction or SYSTEM_INSTRUCTIONS)
            return client.models.generate_content(
                model='gemini-3-flash-preview',
                contents=contents,
                config=config
            )
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                if attempt == max_retries - 1:
                    print("Gemini Quota Exceeded permanently. Please try again later.")
                    raise e
                continue
            else:
                raise e

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

Stream Logic Rules:
1. "Year X" (no suffix) = English Stream ONLY (e.g. Y4).
2. "Xb" or "CE2B" (etc.) = Bilingual Stream ONLY (e.g. Y4b).
3. INSET/PD days = English Stream ONLY (N, R, Y1, Y2, Y3, Y4, Y5, Y6). Bilingual students attend as normal.
4. If an event mentions "all students" or "whole school", include all codes.

Source Management:
- If an event is found in multiple sources (e.g., both a Newsletter and the Term Dates website), consolidate them.
- Store all sources in a 'sources' JSON array field. Each source object: {"title": "string", "date": "string", "time": "string"}.
- Do NOT append source information to the 'full_details' text anymore.
- The 'source_title', 'source_date', and 'source_time' fields should still be populated with the information from the PRIMARY or MOST RECENT source for compatibility.
- REMOVE the 'links' record from your JSON output entirely.

Privacy: If PII detected, return {"status": "REJECTED"}.

Task: Extract all events from the email and its attachments. 

Event Formatting Rules:
1. Dates: YYYY-MM-DD. Include 'formatted_date_display' (e.g. "Mon 20 Apr").
2. Summary: A concise 1-5 sentence overview for the main list view.
3. Full Details: Provide an exhaustive, detailed extraction of all relevant context, notes, specific requirements (e.g., "bring a packed lunch", "wear PE kit", "return books by Monday"), or teacher instructions found in the source. Do not truncate useful secondary information.

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
    "full_details": "string",
    "source_title": "string",
    "source_date": "string",
    "source_time": "string",
    "sources": [
       {"title": "string", "date": "string", "time": "string"}
    ],
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
    print("Connecting to IMAP (imap.hostinger.com)...")
    try:
        mail = imaplib.IMAP4_SSL("imap.hostinger.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        print(f"Logged in successfully as {EMAIL_USER}")
    except Exception as e:
        print(f"IMAP Login failed: {e}")
        return []

    mail.select("inbox")
    print("Searching for UNSEEN emails...")
    
    status, messages = mail.search(None, 'UNSEEN')
    if status != 'OK':
        print(f"IMAP search failed with status: {status}")
        return []

    email_ids = messages[0].split()
    print(f"Found {len(email_ids)} unseen email(s).")
    
    email_data = []
    for e_id in email_ids:
        print(f"Processing email ID: {e_id.decode()}...")
        status, msg_data = mail.fetch(e_id, '(RFC822)')
        if status != 'OK':
            print(f"Failed to fetch email {e_id.decode()}")
            continue

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                subject = decode_header(msg['Subject'])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                
                print(f"Email Subject: {subject}")
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
                            try:
                                body += part.get_payload(decode=True).decode()
                            except:
                                pass
                        elif content_type == "text/html" and not body:
                            # If no plain text yet, try HTML
                            try:
                                html_content = part.get_payload(decode=True).decode()
                                # Simple way to strip some tags for searching keywords
                                body = html_content
                            except:
                                pass
                else:
                    try:
                        body = msg.get_payload(decode=True).decode()
                    except:
                        pass
                
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
                    print(f"Skipping irrelevant email: '{subject}' (From: {from_header_str})")
                    continue

                print(f"Confirmed trusted/relevant email: '{subject}'")
                email_data.append({
                    "subject": subject,
                    "from": from_header_str,
                    "date": date_str,
                    "content": body + "\n" + attachments_text
                })
    print(f"Total relevant emails extracted: {len(email_data)}")
    mail.logout()
    return email_data

def fetch_term_dates():
    print("Fetching official school term dates...")
    url = "https://www.bellevillewix.org.uk/parents-carers/term-dates/"
    try:
        response = requests.get(url, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        # Target the main article or content area
        content = soup.find('article') or soup.find('main') or soup.body
        html_text = content.get_text(separator="\n", strip=True) if content else ""
        
        if len(html_text) < 200:
            print("Term dates page returned very little content. Content might be dynamic.")
            return []

        response = safe_generate_content(
            contents=(
                f"Extract all school term dates, holidays, and INSET/PD days from the following text for the 2025/2026 academic year.\n\n"
                f"FORMAT AS 'insert' ACTIONS.\n"
                f"RULES:\n"
                f"- Set 'classes' to ['All'] for school-wide holidays (Bank holidays, half terms).\n"
                f"- IMPORTANT: INSET/PD days are for English Stream ONLY. Assign only English codes (N, R, Y1, Y2, Y3, Y4, Y5, Y6).\n"
                f"- Set 'type' to 'HOLIDAY' for holidays and 'EVENT' for INSET days.\n"
                f"- Set 'source_title' to 'Official Term Dates Website'.\n"
                f"- Include the source in the 'sources' array as well.\n"
                f"Text: {html_text}"
            )
        )
        
        text = response.text.strip().replace('```json', '').replace('```', '')
        results = json.loads(text)
        return results if isinstance(results, list) else [results]
    except Exception as e:
        print(f"Failed to fetch term dates: {e}")
        return []

def get_existing_events():
    response = supabase.table("events").select("*").execute()
    return response.data

def process_data(email_items, existing_db):
    processed_results = []
    
    for item in email_items:
        print(f"Sending email '{item['subject']}' to Intelligence Engine...")
        # Proactive throttle to avoid hitting RPM limits in Free Tier
        time.sleep(2) 
        
        prompt = (
            f"Existing Database: {json.dumps(existing_db)}\n"
            f"Source Email Title: {item['subject']}\n"
            f"Source Email Date: {item['date']}\n"
            f"Email & Attachment Content: {item['content']}"
        )
        response = safe_generate_content(contents=prompt)
        try:
            text = response.text.strip().replace('```json', '').replace('```', '')
            results = json.loads(text)
            count = len(results) if isinstance(results, list) else 1
            print(f"Intelligence Engine returned {count} record(s) for '{item['subject']}'")
            if isinstance(results, list):
                processed_results.extend(results)
            else:
                processed_results.append(results)
        except Exception as e:
            print(f"Error parsing Gemini response for '{item['subject']}': {e}")
            print(f"Raw Response: {response.text[:200]}...")
            
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
            print(f"Inserting new event: {data.get('title')}")
            supabase.table("events").insert(data).execute()
        elif action == 'update' and item.get('match_id'):
            print(f"Updating existing event (ID: {item.get('match_id')}): {data.get('title')}")
            supabase.table("events").update(data).eq("id", item.get('match_id')).execute()

if __name__ == "__main__":
    if not all([EMAIL_USER, EMAIL_PASS, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
        print("Missing environment variables.")
        exit(1)
        
    email_data = fetch_emails()
    
    # Update last scan time in DB regardless of whether new emails were found
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # Check if record exists
        meta_res = supabase.table("events").select("id").eq("type", "SYSTEM_META").execute()
        if meta_res.data:
            supabase.table("events").update({"summary": now_str}).eq("type", "SYSTEM_META").execute()
        else:
            supabase.table("events").insert({
               "title": "System Meta", 
               "type": "SYSTEM_META", 
               "summary": now_str, 
               "event_date": "1970-01-01", 
               "status": "approved"
            }).execute()
    except Exception as e:
        print(f"Metadata update failed: {e}")

    if email_data:
        db_state = get_existing_events()
        results = process_data(email_data, db_state)
        sync_database(results)
    
    # Process Term Dates (Weekly or when requested)
    term_results = fetch_term_dates()
    if term_results:
        # Check for existing holidays to avoid duplicates
        db_state = get_existing_events()
        # Filter out results that already exist based on title and date
        unique_terms = []
        for tr in term_results:
            if not any(e['title'] == tr['event_data']['title'] and e['event_date'] == tr['event_data']['event_date'] for e in db_state):
                unique_terms.append(tr)
        
        if unique_terms:
            print(f"Syncing {len(unique_terms)} new term/holiday records.")
            sync_database(unique_terms)

    # generate_ics_file() # Disabled for now
    else:
        print("No new emails found.")
