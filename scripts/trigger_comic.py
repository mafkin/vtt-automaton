#!/usr/bin/env python3
import sqlite3
import urllib.request
import json
import sys
import os

DB_PATH = "data/sessions.db"
API_URL = "http://localhost:8771/api/v1/comic"

def get_latest_session():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)
        
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT id, label, started_at FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
    conn.close()
    
    if not row:
        print("Error: No sessions found in database.")
        sys.exit(1)
        
    return dict(row)

def trigger_comic(session_id, test=False):
    url = f"{API_URL}/{session_id}" + ("?test=true" if test else "")
    print(f"Triggering comic generation for session '{session_id}' at {url}...")
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode())
            print(f"Success! API Response: {res_data}")
    except Exception as e:
        print(f"Failed to trigger API: {e}")

if __name__ == "__main__":
    # Usage: trigger_comic.py [--test] [session-id]   (--test: a 1-2 page test run)
    args = sys.argv[1:]
    test = "--test" in args
    args = [a for a in args if a != "--test"]
    if args:
        session_id = args[0]
        print(f"Using provided session ID: {session_id}")
    else:
        print("No session ID provided, looking up the most recent session...")
        session = get_latest_session()
        session_id = session["id"]
        label = session["label"] or "Unnamed Session"
        print(f"Found latest session: '{label}' (ID: {session_id})")
        
    trigger_comic(session_id, test)
