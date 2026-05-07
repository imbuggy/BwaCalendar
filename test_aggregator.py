import unittest
from unittest.mock import patch, MagicMock
import json
import re
from datetime import datetime

# Import the functions from main.py
# Note: Since main.py runs logic on import (the __main__ block is at the bottom), 
# we should be careful. We'll mock the dependencies before importing.
with patch('supabase.create_client'), patch('google.genai.Client'):
    import main

class TestAggregator(unittest.TestCase):

    def setUp(self):
        # Mock Supabase
        main.supabase = MagicMock()
        # Mock Gemini
        main.client = MagicMock()

    def test_pta_ical_regex_parsing(self):
        """Test that the regex in sync_pta_calendar correctly extracts iCal events."""
        with open('test_pta.ics', 'r') as f:
            content = f.read()
        
        # We manually run the regex logic from sync_pta_calendar to verify it works on our sample
        events_raw = re.findall(r"BEGIN:VEVENT.*?END:VEVENT", content, re.DOTALL)
        self.assertEqual(len(events_raw), 2)
        
        ev_str = events_raw[0]
        summary_match = re.search(r"SUMMARY:(.*?)\r?\n", ev_str)
        start_match = re.search(r"DTSTART;VALUE=DATE:(.*?)\r?\n", ev_str)
        desc_match = re.search(r"DESCRIPTION:(.*?)\r?\n", ev_str)
        
        self.assertEqual(summary_match.group(1).strip(), "PTA Summer Fair Planning Meeting")
        self.assertEqual(start_match.group(1).strip(), "20260501")
        self.assertIn("School Hall", desc_match.group(1).strip())

    @patch('requests.get')
    def test_pta_sync_duplicate_check(self, mock_get):
        """Test that sync_pta_calendar checks for existing records."""
        # 1. Setup mock response for iCal
        with open('test_pta.ics', 'r') as f:
            mock_get.return_value.text = f.read()
            mock_get.return_value.status_code = 200
        
        # 2. Setup mock for Supabase (Simulate event already exists)
        main.supabase.table().select().eq().eq().execute.return_value.data = [{'id': 123}]
        
        # 3. Run sync
        main.sync_pta_calendar()
        
        # 4. Verify insert was NOT called because duplicate was found
        main.supabase.table().insert.assert_not_called()

    def test_merge_logic_implementation(self):
        """Test the manual merge logic in sync_database for links and sources."""
        # Mock an existing record in the DB
        existing_event = {
            'id': 999,
            'title': 'Test Event',
            'links': json.dumps([{'title': 'Old Link', 'url': 'http://old.com'}]),
            'sources': json.dumps([{'title': 'Email 1', 'date': '2026-01-01'}])
        }
        main.supabase.table().select().eq().execute.return_value.data = [existing_event]
        
        # New data coming from AI
        new_results = [{
            'action': 'update',
            'match_id': 999,
            'event_data': {
                'title': 'Test Event',
                'links': json.dumps([{'title': 'New Link', 'url': 'http://new.com'}]),
                'sources': json.dumps([{'title': 'Email 2', 'date': '2026-04-29'}])
            }
        }]
        
        # Run sync
        main.sync_database(new_results)
        
        # Verify the update payload sent to Supabase
        update_call = main.supabase.table().update.call_args[0][0]
        
        merged_links = json.loads(update_call['links'])
        merged_sources = json.loads(update_call['sources'])
        
        self.assertEqual(len(merged_links), 2)
        self.assertEqual(len(merged_sources), 2)
        self.assertTrue(any(l['title'] == 'Old Link' for l in merged_links))
        self.assertTrue(any(l['title'] == 'New Link' for l in merged_links))

    @patch('main.safe_generate_content')
    @patch('requests.get')
    def test_term_dates_scraping_flow(self, mock_get, mock_ai):
        """Test the flow of fetching term dates and sending to AI."""
        with open('test_terms.html', 'r') as f:
            mock_get.return_value.content = f.read().encode()
            mock_get.return_value.status_code = 200
            
        mock_ai.return_value.text = json.dumps([{'action': 'insert', 'event_data': {'title': 'Autumn Term Start', 'event_date': '2025-09-02' }}])
        
        results = main.fetch_term_dates()
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['event_data']['title'], 'Autumn Term Start')
        # Ensure AI was called with HTML content
        self.assertIn("Autumn Term", mock_ai.call_args[1]['contents'])

if __name__ == '__main__':
    unittest.main()
