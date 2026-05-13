import os
import imaplib
import email
import re
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
MODEL_NAME = 'gemini-3.1-flash-lite-preview'
client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def normalize_date(date_str):
    """Attempt to normalize various date formats to YYYY-MM-DD.
    Handles formats like 'Thu 07 May', '7th May 2026', '07/05/2026', etc.
    """
    if not date_str: return None
    date_str = date_str.strip()
    
    # 1. Already standard ISO?
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str
        
    # 2. Remove common suffixes and prefixes
    # Remove day of week (e.g., "Thursday ", "Thu ")
    clean = re.sub(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*", "", date_str, flags=re.I)
    # Remove ordinal suffixes (e.g., "7th" -> "7")
    clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", clean)
    
    # 3. Try parsing with common patterns
    now = datetime.now()
    formats = [
        ("%d %B %Y", True),  # 7 May 2026
        ("%d %b %Y", True),  # 7 May 2026 (short month)
        ("%d %B", False),    # 7 May
        ("%d %b", False),    # 7 May (short)
        ("%Y-%m-%d", True),
        ("%d/%m/%Y", True),
        ("%d/%m/%y", True),
        ("%d-%m-%Y", True),
    ]
    
    for fmt, has_year in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            if not has_year:
                # If no year is provided, assume it's the current or next academic year.
                # If we are in May and the event is in Sept, it might be the upcoming year.
                # For simplicity, we default to the current year.
                dt = dt.replace(year=now.year)
            return dt.strftime("%Y-%m-%d")
        except:
            continue
            
    return date_str

def apply_date_constraints(data):
    """Enforces the 1-week duration limit."""
    if not data or not data.get('event_date'):
        return data
    
    if data.get('event_date_end'):
        try:
            start_dt = datetime.strptime(data['event_date'], "%Y-%m-%d")
            end_dt = datetime.strptime(data['event_date_end'], "%Y-%m-%d")
            diff = (end_dt - start_dt).days
            if diff > 7:
                print(f"DEBUG: Restricting event '{data.get('title')}' duration. It spanned {diff} days (Limit: 7). Set end_date to Null.")
                data['event_date_end'] = None
        except:
            pass
    return data

def generate_formatted_date(iso_date):
    """Generates a consistent display date like 'Thu 8 May' from an ISO date."""
    if not iso_date: return None
    try:
        dt_obj = datetime.strptime(iso_date, "%Y-%m-%d")
        # %a is short weekday, %d is day, %b is short month
        # We strip leading zero for a cleaner look
        day = dt_obj.strftime("%d").lstrip('0')
        return dt_obj.strftime(f"%a {day} %b")
    except:
        return iso_date

def find_match(title, date, existing_db):
    """Programmatic check for existing matches to supplement AI matching."""
    if not title or not date: return None
    
    # Simple normalization: lowercase and alphanumeric only
    def clean(s): return re.sub(r'[^a-z0-9]', '', s.lower())
    
    target_clean = clean(title)
    
    for e in existing_db:
        if e.get('type') == 'SYSTEM_META': continue
        
        e_date = e.get('event_date')
        if e_date != date: continue
        
        e_title = e.get('title', '')
        e_title_clean = clean(e_title)
        
        # 1. Exact match
        if e_title == title: return e['id']
        
        # 2. Fuzzy match: One title is contained within the other
        # This catches "Summer Half Term" vs "Mon 25 May Summer Half Term"
        if len(target_clean) > 5 and len(e_title_clean) > 5:
            if target_clean in e_title_clean or e_title_clean in target_clean:
                return e['id']
            
    return None

def safe_generate_content(contents, system_instruction=None):
    """Wrapper for Gemini API with retry logic for 429 rate limits."""
    max_retries = 3
    base_wait = 15 # Increased for higher safety
    
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"Retrying Gemini (Attempt {attempt + 1}). Wait: {base_wait * attempt}s")
                time.sleep(base_wait * attempt)
            
            config = types.GenerateContentConfig(
                system_instruction=system_instruction or SYSTEM_INSTRUCTIONS,
                temperature=0.1, # Low temperature for consistent extraction
                thinking_config=types.ThinkingConfig(include_thoughts=False), # Minimal reasoning to save costs
                response_mime_type="application/json"
            )
            return client.models.generate_content(
                model=MODEL_NAME,
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

def get_system_meta():
    """Retrieves metadata stored in the SYSTEM_META record."""
    try:
        res = supabase.table("events").select("*").eq("type", "SYSTEM_META").execute()
        if res.data:
            meta = res.data[0]
            try:
                # Store extra state in the 'full_details' field as JSON
                return json.loads(meta.get('full_details') or '{}')
            except:
                return {}
    except Exception as e:
        print(f"Error fetching system meta: {e}")
    return {}

def update_system_meta(new_data):
    """Updates metadata in the SYSTEM_META record."""
    try:
        current = get_system_meta()
        current.update(new_data)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        payload = {
            "title": "System Meta",
            "type": "SYSTEM_META",
            "summary": now_str, # Used for UI 'Last Updated' display
            "full_details": json.dumps(current),
            "event_date": "1970-01-01",
            "status": "approved"
        }
        
        res = supabase.table("events").select("id").eq("type", "SYSTEM_META").execute()
        if res.data:
            supabase.table("events").update(payload).eq("id", res.data[0]['id']).execute()
        else:
            supabase.table("events").insert(payload).execute()
    except Exception as e:
        print(f"Error updating system meta: {e}")

SYSTEM_INSTRUCTIONS = """
Role: School Calendar Aggregator. 
Extract school events with ZERO-TOLERANCE for schema errors.
STRICTLY RETAIN TIMES: If an event mentions a specific time (e.g. "9:00am", "3:30pm", "after school"), you MUST extract it into 'time_value'. DO NOT strip times.

STRICT SCHEMA RULES:
Required JSON Structure: Array of {
  "action": "insert"|"update",
  "match_id": int|null,
  "status": "approved"|"REJECTED",
  "event_data": {
    "title": string,
    "event_date": "YYYY-MM-DD (STRICT: No 'Thu', no '7th', just ISO format)",
    "event_date_end": "YYYY-MM-DD"|null,
    "formatted_date_display": "string (Human readable, MUST include day of week e.g. 'Thu 7 May')",
    "time_type": "all-day"|"single"|"range",
    "time_value": string,
    "classes": string[] (e.g. ["Y1", "Y2b", "All"]),
    "summary": string,
    "full_details": string,
    "type": "HOLIDAY"|"ACADEMIC"|"SPORTS"|"COMMUNITY"|"ARTS"|"TRIP"|"WELLBEING"|"ADMIN"|"OTHER",
    "is_deadline": boolean,
    "deadline_desc": string|null,
    "source_title": string,
    "source_date": string,
    "source_time": string,
    "sources": [{"title": string, "date": string, "time": string}]
  }
}

NEGATIVE CONSTRAINTS:
- NEVER use the key "category". You MUST use "type".
- NEVER use markdown outside the JSON block.
- NEVER truncate titles or critical details.
- PRIVACY & PII RULES: 
  * REJECT (status="REJECTED"): Any email that is purely private/individual. This includes medical details for a specific child, disciplinary issues, or any communication addressed to only ONE parent and NOT a group/class/school stream.
  * ANONYMIZE & APPROVE (status="approved"): School-wide or class-wide announcements (e.g. forward from teacher, newsletters) that happen to contain parent/child names.
  * SCRUBBING: You MUST anonymize/scrub all FORBIDDEN PII from "title", "summary", and "full_details". 
  * Replace full student names with initials (e.g. "JS") or generic terms like "the student".
  * Replace parent names with generic terms like "[Parent]".
  * REMOVE personal phone numbers or home addresses.
  * ALLOWED (Do not scrub): Teacher names (e.g. Mr Hennessy), School personnel, School address, School phone, School official emails.
  * Ignore metadata headers ("To:", "From:", "Subject:") when checking for PII.

Logic:
- INSET/PD = English Stream ONLY (N, R, Y1, Y2, Y3, Y4, Y5, Y6).
- Bilingual Class Mapping: ALWAYS use UK names. Normalize according to:
  * MSB -> Rb
  * GSB -> Y1b
  * CPB -> Y2b
  * CE1B -> Y3b
  * CE2B -> Y4b
  * CM1B -> Y5b
  * CM2B -> Y6b
  * 1B -> Y1b
  * 2B -> Y2b
  * 3B -> Y3b
  * 4B -> Y4b
  * 5B -> Y5b
  * 6B -> Y6b
  * Nursery -> N
  * Reception -> R
  * Yr1/Year1/Year 1 -> Y1
  * Yr2/Year2/Year 2 -> Y2
  * Yr3/Year3/Year 3 -> Y3
  * Yr4/Year4/Year 4 -> Y4
  * Yr5/Year5/Year 5 -> Y5
  * Yr6/Year6/Year 6 -> Y6
- MULTI-DATE LISTS: If an email lists multiple dates for an event (e.g., Father's Day on Wed 17th and Friday 26th for different classes), you MUST create a SEPARATE event object for EACH date/class combination. Do NOT lump them into a single event.
- SOURCES: Events can have multiple sources in the 'sources' array. When updating an event, retain all existing sources and append the new source.
- PRIORITY EVENTS: Always mark Parent-Teacher Meetings, Consultations, and Individual Appointments as "is_deadline": true.
- "X Stream finishes at..." -> Split into separate events per stream.
- MAX DURATION: Events MUST NOT stretch more than 7 days. If an event in the source material covers a longer period (e.g. 'Summer Term', 'Extra-curricular clubs start April-July'), you MUST only extract the START DATE and set 'event_date_end' to null.
- SPECIAL EVENT: 'International families day' is a special event. You MUST:
  * Append ' (Dress up required)' to the title if not already present.
  * Mention 'Dress up is required' in the summary and full_details.
  * Set 'is_deadline' to true.
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
    """Returns a list of email IDs to process."""
    print("Connecting to IMAP (imap.hostinger.com)...")
    try:
        mail = imaplib.IMAP4_SSL("imap.hostinger.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        print(f"Logged in successfully as {EMAIL_USER}")
    except Exception as e:
        print(f"IMAP Login failed: {e}")
        return None, []

    mail.select("inbox")
    print("Searching for UNSEEN emails...")
    
    status, messages = mail.search(None, 'UNSEEN')
    if status != 'OK':
        print(f"IMAP search failed with status: {status}")
        return mail, []

    email_ids = messages[0].split()
    print(f"Found {len(email_ids)} unseen email(s).")
    return mail, email_ids

def parse_single_email(mail, e_id):
    """Fetches and parses a single email without marking it as seen."""
    print(f"Processing email ID: {e_id.decode()}...")
    # Use BODY.PEEK[] to keep the email as UNSEEN until we confirm processing success
    status, msg_data = mail.fetch(e_id, '(BODY.PEEK[])')
    if status != 'OK':
        print(f"Failed to fetch email {e_id.decode()}")
        return None

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
                        try:
                            html_content = part.get_payload(decode=True).decode()
                            body = html_content
                        except:
                            pass
            else:
                try:
                    body = msg.get_payload(decode=True).decode()
                except:
                    pass
            
            from_header_str = msg.get('From', '').lower()
            reply_to_str = msg.get('Reply-To', '').lower()
            content_lower = (subject + " " + body + " " + attachments_text).lower()
            
            admin_id_1 = "".join([chr(x) for x in [97, 100, 109, 105, 110, 64, 119, 105, 120, 46, 119, 97, 110, 100, 115, 119, 111, 114, 116, 104, 46, 115, 99, 104, 46, 117, 107]])
            admin_id_2 = "".join([chr(x) for x in [97, 100, 109, 105, 110, 64, 98, 101, 108, 108, 101, 118, 105, 108, 108, 101, 119, 105, 120, 46, 113, 49, 101, 46, 111, 114, 103, 46, 117, 107]])
            trusted_identifiers = ['schoolcomms.com', 'belleville wix academy', 'lyceefrancais.org.uk', admin_id_1, admin_id_2]
            
            is_trusted = any(id in from_header_str or id in reply_to_str for id in trusted_identifiers)
            is_forwarded_from_trusted = any(id in content_lower for id in trusted_identifiers)
            
            if not (is_trusted or is_forwarded_from_trusted):
                print(f"Skipping irrelevant email: '{subject}'")
                # Mark as seen so we don't process irrelevant emails again
                mail.store(e_id, '+FLAGS', '\\Seen')
                return None

            print(f"Confirmed trusted/relevant email: '{subject}'")
            return {
                "subject": subject,
                "from": from_header_str,
                "date": date_str,
                "content": body + "\n" + attachments_text
            }
    return None

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
                f"- Bilingual Class Mapping: ALWAYS use UK names for bilingual entries (MSB->Rb, GSB->Y1b, CPB->Y2b, CE1B->Y3b, CE2B->Y4b, CM1B->Y5b, CM2B->Y6b, 1B->Y1b, 2B->Y2b).\n"
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

def sync_pta_calendar():
    """Syncs PTA events from their ical feed."""
    print("Starting PTA Calendar sync (Python)...")
    try:
        url = "https://bwapta.co.uk/events/list/?ical=1"
        response = requests.get(url, timeout=15)
        content = response.text
        
        # Simple regex-based ICS parser for VEVENTs
        # This extracts SUMMARY, DTSTART, and DESCRIPTION
        events_raw = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", content, re.DOTALL)
        
        added_count = 0
        for ev_str in events_raw:
            summary_match = re.search(r"SUMMARY:(.*?)\r?\n", ev_str)
            start_match = re.search(r"DTSTART;VALUE=DATE:(.*?)\r?\n", ev_str)
            if not start_match:
                start_match = re.search(r"DTSTART:(.*?)\r?\n", ev_str)
            
            desc_match = re.search(r"DESCRIPTION:(.*?)\r?\n", ev_str)
            url_match = re.search(r"URL:(.*?)\r?\n", ev_str)

            if not summary_match or not start_match:
                continue

            title = summary_match.group(1).strip().replace('\\', '')
            raw_date = start_match.group(1).strip()
            
            # Format date YYYYMMDD or YYYYMMDDTHHMMSS
            event_date = normalize_date(raw_date)
            formatted_date = generate_formatted_date(event_date)
            
            # Extract end date if present (ICS format)
            end_date = None
            end_match = re.search(r"DTEND;VALUE=DATE:(.*?)\r?\n", ev_str)
            if not end_match:
                end_match = re.search(r"DTEND:(.*?)\r?\n", ev_str)
            
            if end_match:
                # iCal end dates are exclusive, so we don't necessarily need to subtract 1 day 
                # but if it stretches > 7 days we'll clip it in the constraints.
                end_date_val = end_match.group(1).strip()
                # If it's a date-only field like 20260710, normalize_date will handle it
                end_date = normalize_date(end_date_val)

            # Extract time from ICS DTSTART if present
            time_val = ""
            time_type = "all-day"
            if 'T' in raw_date:
                try:
                    # e.g. 20260507T100000Z
                    time_part = raw_date.split('T')[1].replace('Z','')
                    hh = time_part[:2]
                    mm = time_part[2:4]
                    time_val = f"{hh}:{mm}"
                    time_type = "single"
                except: pass

            description = desc_match.group(1).strip().replace('\\n', '\n').replace('\\', '') if desc_match else ""
            ev_url = url_match.group(1).strip() if url_match else url

            # Check for duplicates by title and date
            match_id = find_match(title, event_date, get_existing_events())
            if not match_id:
                print(f"Adding PTA event: {title}")
                data = {
                    "title": title,
                    "event_date": event_date,
                    "event_date_end": end_date,
                    "formatted_date_display": formatted_date,
                    "time_value": time_val,
                    "time_type": time_type,
                    "summary": title,
                    "full_details": description,
                    "type": "PTA",
                    "status": "approved",
                    "classes": ["All"], # Default for PTA
                    "source_title": "PTA Website",
                    "source_date": datetime.now().strftime("%Y-%m-%d"),
                    "links": json.dumps([{"title": "PTA Event Link", "url": ev_url}])
                }
                
                # Apply 1-week restriction
                data = apply_date_constraints(data)
                
                supabase.table("events").insert(data).execute()
                added_count += 1
            else:
                # Update existing PTA event to ensure formatted_date_display is set
                supabase.table("events").update({"formatted_date_display": formatted_date}).eq("id", match_id).execute()
        
        print(f"PTA sync complete. Added {added_count} new events.")
        return True
    except Exception as e:
        print(f"Failed PTA sync: {e}")
        return False

def get_existing_events():
    response = supabase.table("events").select("*").execute()
    return response.data

def process_batch(items, existing_db):
    """Processes multiple items in a single Gemini call to save credits."""
    if not items: return []
    
    print(f"Batch processing {len(items)} items to save AI credits...")
    
    # Construct a single prompt for all items
    # Provide upcoming events for context (up to 200) to help AI identify matches
    now_str = datetime.now().strftime("%Y-%m-%d")
    upcoming = [e for e in existing_db if e.get('event_date', '') >= now_str]
    db_summary = [{"t": e['title'], "d": e['event_date'], "id": e['id']} for e in upcoming[:200]]
    
    batch_prompt = f"Existing Database Snippet (Upcoming Events): {json.dumps(db_summary)}\n\n"
    batch_prompt += "INSTRUCTIONS: If a new item matches an existing entry (same date and same/similar title), use action='update' and provide the match_id.\n"
    batch_prompt += "Note: Holidays like 'Summer Half Term' are often week-long; if you find a match starting on the same date, MERGE them.\n\n"
    for i, item in enumerate(items):
        batch_prompt += f"--- ITEM {i+1} ---\n"
        batch_prompt += f"Source: {item.get('subject')}\n"
        batch_prompt += f"Date: {item.get('date')}\n"
        batch_prompt += f"Content: {item.get('content')[:6000]}\n\n"

    try:
        response = safe_generate_content(contents=batch_prompt)
        text = response.text.strip().replace('```json', '').replace('```', '')
        results = json.loads(text)
        return results if isinstance(results, list) else [results]
    except Exception as e:
        print(f"Batch processing failed: {e}")
        return None

def sync_database(results):
    """Syncs results to Supabase. Returns True if ALL operations succeeded."""
    if not results: return True
    
    success = True
    db_state = get_existing_events()
    
    for item in results:
        try:
            if item.get('status') == 'REJECTED':
                print("PII Detected. Record rejected.")
                continue
                
            action = item.get('action')
            data = item.get('event_data')
            if not data: continue

            # Normalize event_date to YYYY-MM-DD
            if 'event_date' in data:
                data['event_date'] = normalize_date(data['event_date'])
                # Always ensure a clean formatted_date_display
                if not data.get('formatted_date_display') or len(data['formatted_date_display']) > 15:
                    data['formatted_date_display'] = generate_formatted_date(data['event_date'])

            if data.get('event_date_end'):
                data['event_date_end'] = normalize_date(data['event_date_end'])

            # Apply 1-week restriction
            data = apply_date_constraints(data)

            # Safety Remap: AI sometimes uses 'category' instead of 'type'
            if 'category' in data and 'type' not in data:
                data['type'] = data.pop('category')

            match_id = item.get('match_id')
            
            # Programmatic double-check for matches before inserting
            if action == 'insert':
                possible_match = find_match(data.get('title'), data.get('event_date'), db_state)
                if possible_match:
                    print(f"Insertion double-check found match (ID: {possible_match}). Switching to update.")
                    action = 'update'
                    match_id = possible_match

            if action == 'insert':
                print(f"Inserting new event: {data.get('title')}")
                data['status'] = data.get('status', 'approved').strip()
                supabase.table("events").insert(data).execute()
                # Update local state to prevent duplicates in same batch
                db_state.append({"id": 0, "title": data['title'], "event_date": data['event_date']}) 
            elif action == 'update' and match_id:
                print(f"Merging new data into existing event (ID: {match_id}): {data.get('title')}")
                
                # Fetch existing to merge
                res = supabase.table("events").select("*").eq("id", match_id).execute()
                if res.data:
                    existing = res.data[0]
                    # Ensure status is approved in updates
                    data['status'] = 'approved'

                    # MERGE RANGE: If existing has end date and new doesn't, keep existing
                    if existing.get('event_date_end') and not data.get('event_date_end'):
                        data['event_date_end'] = existing.get('event_date_end')
                    
                    # MERGE TIME: If existing has time and new doesn't, keep existing
                    if existing.get('time_value') and not data.get('time_value'):
                        data['time_value'] = existing.get('time_value')
                        data['time_type'] = existing.get('time_type', 'all-day')
                    
                    # Merge logic:
                    # 1. Links: Combine unique ones
                    # 2. Sources: Combine unique ones
                    # 3. Details: if new is shorter, keep old or append? 
                    # We'll trust Gemini to provide 'data' as the NEW desired state, 
                    # but we'll manually merge critical arrays here if AI missed them.
                    
                    try:
                        old_links = json.loads(existing.get('links') or '[]')
                        new_links = json.loads(data.get('links') or '[]')
                        combined_links = {json.dumps(l, sort_keys=True): l for l in old_links + new_links}.values()
                        data['links'] = json.dumps(list(combined_links))
                    except: pass

                    try:
                        old_sources = json.loads(existing.get('sources') or '[]')
                        new_sources = json.loads(data.get('sources') or '[]')
                        combined_sources = {json.dumps(s, sort_keys=True): s for s in old_sources + new_sources}.values()
                        data['sources'] = json.dumps(list(combined_sources))
                    except: pass
                
                supabase.table("events").update(data).eq("id", match_id).execute()
        except Exception as e:
            print(f"Database sync failed for record: {e}")
            success = False
            
    return success

def deduplicate_database():
    """Fetches upcoming events and asks Gemini to identify and merge duplicates. 
    Always looks back at least 7 days from today to ensure recent duplicates are caught.
    """
    print("Initiating Stateful Deduplication Pass...")
    try:
        now_date_obj = datetime.now()
        now_date = now_date_obj.strftime("%Y-%m-%d")
        
        # Look back 7 days to catch duplicates arriving late
        from datetime import timedelta
        lookback_date = (now_date_obj - timedelta(days=7)).strftime("%Y-%m-%d")
        
        res = supabase.table("events").select("*").gte("event_date", lookback_date).order("event_date").execute()
        events = res.data
        if not events:
            return

        # Group by date (normalized)
        grouped = {}
        for e in events:
            # Skip system meta during dedup
            if e.get('type') == 'SYSTEM_META': continue
            
            d = normalize_date(e['event_date'])
            if not d: continue
            
            if d not in grouped: grouped[d] = []
            grouped[d].append({
                "id": e['id'],
                "title": e['title'],
                "time": e.get('time_value', ''),
                "time_type": e.get('time_type', 'all-day'),
                "classes": e['classes'],
                "summary": e['summary'],
                "full_details": e.get('full_details', ''),
                "links": e.get('links', '[]'),
                "sources": e.get('sources', '[]')
            })

        # Process a batch of dates
        processed_count = 0
        sorted_dates = sorted(grouped.keys())
        
        for date in sorted_dates:
            items = grouped[date]
            if len(items) < 2: 
                continue
            
            if processed_count >= 30: # Increased batch size
                print(f"Deduplication batch limit reached (30 dates). Will continue next run.")
                break
                
            print(f"Checking for duplicates on {date} ({len(items)} events)...")
            prompt = (
                f"Review the following school events for the date {date}. "
                f"Identify entries that refer to the SAME event even if titles are slightly different. \n\n"
                f"DUPLICATE EXAMPLES: \n"
                f"- 'Parent Gym: Week 1' and 'Parent Gym Week 1: Chat' ARE THE SAME.\n"
                f"- 'Summer Half Term' and 'Monday 25 May Summer Half Term' ARE THE SAME.\n"
                f"- 'Inset Day' and 'PD Day' on same date ARE THE SAME.\n\n"
                f"Note on Times: If one entry has a specific time (e.g. 9:00am) and the other doesn't, ensure the merged entry RETAINS the most specific time.\n\n"
                f"Events: {json.dumps(items)}"
            )
            
            response = safe_generate_content(
                contents=prompt,
                system_instruction=(
                    "You are a school calendar deduplication expert. Identify duplicates for the same day. "
                    "FUZZY MATCHING: Treat events as identical if they share the same date and have highly similar core titles (e.g. same topic, same workshop name). "
                    "Ignore minor differences in punctuation, prefixes like 'Fwd:' or 'Newsletter', or appended details like ': Topic Name'. "
                    "Instead of just deleting, you MUST MERGE the information. "
                    "1. Keep the most descriptive, complete title. "
                    "2. Combine summaries into the most coherent and detailed one. "
                    "3. Append unique information from all full_details fields. "
                    "4. Combine and deduplicate ALL entries in the 'links' and 'sources' arrays into a single combined array. "
                    "5. Retain the most specific 'time_value' and 'time_type'. "
                    "6. Ensure 'event_date' remains {date}. "
                    "Return JSON: {'deletes': [ids_to_remove], 'updates': [{'id': primary_id, 'merged_data': {full_event_object}}]}"
                )
            )
            
            if not response: 
                print("Gemini failed during deduplication. Stopping batch.")
                break

            try:
                text = response.text.strip().replace('```json', '').replace('```', '')
                plan = json.loads(text)
                
                # Execute Plan
                for d_id in plan.get('deletes', []):
                    supabase.table("events").delete().eq("id", d_id).execute()
                for up in plan.get('updates', []):
                    if up.get('id') and up.get('merged_data'):
                        # Ensure ID is not in merged_data to avoid DB errors
                        m_data = up['merged_data']
                        if 'id' in m_data: del m_data['id']
                        
                        # Normalize merged dates
                        if 'event_date' in m_data:
                            m_data['event_date'] = normalize_date(m_data['event_date'])
                            m_data['formatted_date_display'] = generate_formatted_date(m_data['event_date'])
                        if m_data.get('event_date_end'):
                            m_data['event_date_end'] = normalize_date(m_data['event_date_end'])
                            
                        supabase.table("events").update(m_data).eq("id", up['id']).execute()
                
                processed_count += 1
                time.sleep(1) # Small throttle
                        
            except Exception as e:
                print(f"Deduplication apply error for {date}: {e}")
                
    except Exception as e:
        print(f"Deduplication pass failed: {e}")

def generate_static_ical_files():
    """Generates static .ics files from Supabase for GitHub Pages hosting."""
    try:
        print("Generating static iCal files for GitHub Pages...")
        res = supabase.table("events").select("*").eq("status", "approved").neq("type", "SYSTEM_META").execute()
        all_events = res.data or []
        
        # Ensure output directory exists
        os.makedirs("api/calendar", exist_ok=True)
        
        def format_ical_event(e, selected_classes):
            title = e.get('title') or 'Event'
            
            # Determine prefix
            prefix = "BWA"
            e_classes = e.get('classes') or []
            if isinstance(e_classes, str):
                try: e_classes = json.loads(e_classes)
                except: e_classes = [e_classes]
            
            if "All" not in e_classes:
                matching = [c for c in e_classes if c in selected_classes]
                if matching: prefix = f"BWA {matching[0]}"

            # Clean raw title of any existing BWA prefixes to avoid duplication
            # We check for various forms: "BWA: ", "BWA ", "BWA-" etc.
            title_clean = title.strip()
            # Regex to match "BWA" followed by optional class/char, and optional colon/space
            # Example: "BWA: ", "BWA Y1: ", "BWA "
            match = re.match(r'^BWA(\s+[\w\d]+)?[:\s\-]*', title_clean, re.IGNORECASE)
            if match:
                title_clean = title_clean[match.end():].strip()
            
            safe_title = title_clean.replace(':', ' - ')

            dt_start = (e.get('event_date') or '1970-01-01').replace('-', '')
            # iCal end dates are exclusive (non-inclusive)
            dt_end = dt_start
            if e.get('event_date_end'):
                try:
                    from datetime import timedelta
                    end_dt = datetime.strptime(e.get('event_date_end'), "%Y-%m-%d") + timedelta(days=1)
                    dt_end = end_dt.strftime("%Y%m%d")
                except: pass
            else:
                # Single day event: end is next day
                try:
                    from datetime import timedelta
                    end_dt = datetime.strptime(e.get('event_date'), "%Y-%m-%d") + timedelta(days=1)
                    dt_end = end_dt.strftime("%Y%m%d")
                except: pass

            summary = e.get('summary') or ''
            full = e.get('full_details') or ''
            desc = (summary + "\\n\\n" + full).replace('\n', '\\n').replace('\r', '')
            
            return f"BEGIN:VEVENT\nUID:{e['id']}@bwa-calendar\nDTSTART;VALUE=DATE:{dt_start}\nDTEND;VALUE=DATE:{dt_end}\nSUMMARY:{prefix}: {safe_title}\nDESCRIPTION:{desc}\nCATEGORIES:{e.get('type','OTHER')}\nEND:VEVENT"

        def create_ics_content(events, class_list):
            body = "\n".join([format_ical_event(e, class_list) for e in events])
            return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//BWA-Calendar-Sync//EN\nCALSCALE:GREGORIAN\n{body}\nEND:VCALENDAR"

        class_names = ['N', 'R', 'Rb', 'Y1', 'Y1b', 'Y2', 'Y2b', 'Y3', 'Y3b', 'Y4', 'Y4b', 'Y5', 'Y5b', 'Y6', 'Y6b']
        
        # 1. Generate 'All.ics'
        with open("api/calendar/All.ics", "w", encoding="utf-8") as f:
            f.write(create_ics_content(all_events, class_names))
        
        # 2. Generate individual class files
        for cls in class_names:
            cls_events = [e for e in all_events if "All" in (e.get('classes') or []) or cls in (e.get('classes') or [])]
            with open(f"api/calendar/{cls}.ics", "w", encoding="utf-8") as f:
                f.write(create_ics_content(cls_events, [cls]))
        
        print("Static iCal generation successful.")
    except Exception as e:
        print(f"Error generating static iCal files: {e}")

if __name__ == "__main__":
    if not all([EMAIL_USER, EMAIL_PASS, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
        print("Missing environment variables.")
        exit(1)
        
    meta = get_system_meta()
    now = datetime.now()
    
    # 1. PROCESS EMAILS (Multiple times a day)
    # This always runs when the script is called.
    mail, email_ids = fetch_emails()
    if email_ids and mail:
        db_state = get_existing_events()
        batch_items = []
        processed_ids = []
        
        for e_id in email_ids:
            item = parse_single_email(mail, e_id)
            if item:
                batch_items.append(item)
                processed_ids.append(e_id)
            
            if len(batch_items) >= 3:
                results = process_batch(batch_items, db_state)
                if results and sync_database(results):
                    for pid in processed_ids:
                        mail.store(pid, '+FLAGS', '\\Seen')
                    db_state = get_existing_events()
                batch_items = []
                processed_ids = []
                time.sleep(10)

        if batch_items:
            results = process_batch(batch_items, db_state)
            if results and sync_database(results):
                for pid in processed_ids:
                    mail.store(pid, '+FLAGS', '\\Seen')
            time.sleep(5)

    if mail:
        mail.logout()
    
    # 2. PTA CALENDAR SYNC (Once a day)
    last_pta_check = meta.get('last_pta_check')
    pta_due = True
    if last_pta_check:
        try:
            last_pta_dt = datetime.strptime(last_pta_check, "%Y-%m-%d")
            if (now - last_pta_dt).days < 1:
                pta_due = False
        except: pass
    
    if pta_due:
        if sync_pta_calendar():
            update_system_meta({"last_pta_check": now.strftime("%Y-%m-%d")})
    else:
        print("Skipping PTA sync (already done today).")

    # 3. TERM DATES CHECK (Once a month)
    last_term_check = meta.get('last_term_check')
    term_due = True
    if last_term_check:
        try:
            last_dt = datetime.strptime(last_term_check, "%Y-%m-%d")
            if (now - last_dt).days < 30:
                term_due = False
        except: pass

    if term_due:
        term_results = fetch_term_dates()
        if term_results:
            # use sync_database to handle merges/matches correctly
            print(f"Syncing {len(term_results)} term/holiday records...")
            sync_database(term_results)
            update_system_meta({"last_term_check": now.strftime("%Y-%m-%d")})
    else:
        print("Skipping term dates check (already done this month).")

    # Run Deduplication Pass
    deduplicate_database()
    
    # Generate static files for GitHub Pages
    generate_static_ical_files()
    
    print(f"Aggregator Run Complete at {now.strftime('%Y-%m-%d %H:%M:%S')}")
