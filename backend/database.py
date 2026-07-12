import os
import socket
from pathlib import Path
from fastapi import HTTPException
import requests
import urllib3.util.connection

# Force urllib3 to only use IPv4 to prevent 20-second connection timeouts on systems with broken IPv6 routes
urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET

# Load environment variables from .env file if it exists
dotenv_path = Path(__file__).parent / ".env"
if not dotenv_path.exists():
    # Fallback to root directory .env if running from workspace parent
    dotenv_path = Path(__file__).parent.parent / ".env"

if dotenv_path.exists():
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ[key] = val

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Warning: SUPABASE_URL or SUPABASE_KEY not set in .env. Database operations will fail.")

def query_supabase(path: str, method: str = "GET", json_data: dict = None, params: dict = None) -> list:
    """
    Query Supabase REST API directly via requests.
    Prevents httpx/anyio event loop deadlocks in synchronous FastAPI workers.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Database credentials not configured")
        
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"  # Returns full representation of inserted/patched records
    }
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    try:
        r = requests.request(method, url, headers=headers, json=json_data, params=params, timeout=10)
        r.raise_for_status()
        if r.text:
            return r.json()
        return []
    except Exception as e:
        print(f"Supabase REST error: {e}")
        try:
            detail = r.json().get("message", str(e))
        except Exception:
            detail = str(e)
        raise HTTPException(status_code=500, detail=f"Database query failed: {detail}")
