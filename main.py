"""
SysVitals — backend with Supabase Auth & Device Management
"""

import os
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import bcrypt
import requests

# Force urllib3 to only use IPv4 to prevent 20-second connection timeouts on systems with broken IPv6 routes
import socket
import urllib3.util.connection
urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET

# Load environment variables from .env file if it exists
dotenv_path = Path(__file__).parent / ".env"
if dotenv_path.exists():
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Remove surrounding quotes
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
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
        # Extract body message if exists
        try:
            detail = r.json().get("message", str(e))
        except Exception:
            detail = str(e)
        raise HTTPException(status_code=500, detail=f"Database query failed: {detail}")

app = FastAPI(title="SysVitals")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Request Models ---

class UserAuth(BaseModel):
    username: str
    password: str

class DeviceRegister(BaseModel):
    user_id: str
    device_name: str
    hostname: Optional[str] = None

class TelemetryIngest(BaseModel):
    device_secret: str
    cpu_temp: Optional[float] = None
    cpu_power: Optional[float] = None
    cpu_clock: Optional[float] = None
    cpu_util: Optional[float] = None
    gpu_name: Optional[str] = None
    gpu_temp: Optional[float] = None
    gpu_power: Optional[float] = None
    gpu_util: Optional[float] = None
    gpu_mem_used: Optional[float] = None
    gpu_mem_total: Optional[float] = None
    gpu_active: Optional[bool] = None
    ac_plugged: Optional[bool] = None
    battery_power: Optional[float] = None
    battery_voltage: Optional[float] = None
    battery_level: Optional[float] = None
    power_mode: str

# In-memory storage for the latest readings by device_id
latest_reading = {}

# --- Auth Endpoints ---

@app.post("/api/register")
def register(auth: UserAuth):
    # Check if user already exists
    users = query_supabase("users", params={"username": f"eq.{auth.username}", "select": "id"})
    if users:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Hash password with bcrypt
    salt = bcrypt.gensalt()
    pwd_hash = bcrypt.hashpw(auth.password.encode('utf-8'), salt).decode('utf-8')

    # Insert user
    query_supabase("users", method="POST", json_data={
        "username": auth.username,
        "password_hash": pwd_hash
    })
    return {"success": True, "message": "User registered successfully"}

@app.post("/api/login")
def login(auth: UserAuth):
    users = query_supabase("users", params={"username": f"eq.{auth.username}", "select": "id,password_hash"})
    if not users:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user = users[0]
    # Verify bcrypt password
    if not bcrypt.checkpw(auth.password.encode('utf-8'), user["password_hash"].encode('utf-8')):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {"success": True, "user_id": user["id"]}

# --- Device Endpoints ---

@app.post("/api/device/register")
def register_device(device: DeviceRegister):
    # Register device (PostgreSQL automatically generates device_secret)
    res = query_supabase("devices", method="POST", json_data={
        "user_id": device.user_id,
        "name": device.device_name,
        "hostname": device.hostname
    })
    if not res:
        raise HTTPException(status_code=500, detail="Failed to register device")
        
    return {"device_secret": res[0]["device_secret"]}

@app.get("/api/user/{user_id}/devices")
def get_user_devices(user_id: str):
    return query_supabase("devices", params={
        "user_id": f"eq.{user_id}",
        "select": "id,name,hostname,created_at,last_seen"
    })

# --- Telemetry Ingest & Fetch Endpoints ---

@app.post("/api/ingest")
def ingest(payload: TelemetryIngest):
    # 1. Lookup device
    devices = query_supabase("devices", params={
        "device_secret": f"eq.{payload.device_secret}",
        "select": "id,name,hostname"
    })
    if not devices:
        raise HTTPException(status_code=401, detail="Invalid device secret")
    
    device = devices[0]
    device_id = device["id"]
    
    now_iso = datetime.now(timezone.utc).isoformat()
    
    # 2. Update device last_seen
    query_supabase("devices", method="PATCH", json_data={
        "last_seen": now_iso
    }, params={"id": f"eq.{device_id}"})
    
    # 3. Log telemetry record in DB
    t_data = {
        "device_id": device_id,
        "ts": now_iso,
        "cpu_temp": payload.cpu_temp,
        "cpu_power": payload.cpu_power,
        "cpu_clock": payload.cpu_clock,
        "cpu_util": payload.cpu_util,
        "gpu_name": payload.gpu_name,
        "gpu_temp": payload.gpu_temp,
        "gpu_power": payload.gpu_power,
        "gpu_util": payload.gpu_util,
        "gpu_mem_used": payload.gpu_mem_used,
        "gpu_mem_total": payload.gpu_mem_total,
        "gpu_active": payload.gpu_active,
        "ac_plugged": payload.ac_plugged,
        "battery_power": payload.battery_power,
        "battery_voltage": payload.battery_voltage,
        "battery_level": payload.battery_level,
        "power_mode": payload.power_mode
    }
    query_supabase("telemetry", method="POST", json_data=t_data)
    
    # 4. Cache latest reading in memory
    global latest_reading
    latest_reading[device_id] = {
        "ts": time.time(),
        "hostname": device.get("hostname") or device["name"],
        "cpu_temp": payload.cpu_temp,
        "power_mode": payload.power_mode,
        "cpu_power": payload.cpu_power,
        "cpu_clock": payload.cpu_clock,
        "cpu_util": payload.cpu_util,
        "gpu_name": payload.gpu_name,
        "gpu_temp": payload.gpu_temp,
        "gpu_power": payload.gpu_power,
        "gpu_util": payload.gpu_util,
        "gpu_mem_used": payload.gpu_mem_used,
        "gpu_mem_total": payload.gpu_mem_total,
        "gpu_active": payload.gpu_active,
        "ac_plugged": payload.ac_plugged,
        "battery_power": payload.battery_power,
        "battery_voltage": payload.battery_voltage,
        "battery_level": payload.battery_level,
    }
    return {"status": "ok"}

@app.get("/api/device/{device_id}/latest")
def get_device_latest(device_id: str):
    if device_id in latest_reading:
        return latest_reading[device_id]
        
    records = query_supabase("telemetry", params={
        "device_id": f"eq.{device_id}",
        "order": "ts.desc",
        "limit": 1
    })
    if not records:
        raise HTTPException(status_code=404, detail="No telemetry data found for this device")
    
    record = records[0]
    try:
        ts_str = record["ts"].replace("+00:00", "Z")
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        ts_float = dt.timestamp()
    except Exception:
        ts_float = time.time()
        
    res_data = {
        "ts": ts_float,
        "cpu_temp": record["cpu_temp"],
        "power_mode": record["power_mode"],
        "cpu_power": record["cpu_power"],
        "cpu_clock": record["cpu_clock"],
        "cpu_util": record["cpu_util"],
        "gpu_name": record["gpu_name"],
        "gpu_temp": record["gpu_temp"],
        "gpu_power": record["gpu_power"],
        "gpu_util": record["gpu_util"],
        "gpu_mem_used": record["gpu_mem_used"],
        "gpu_mem_total": record["gpu_mem_total"],
        "gpu_active": record["gpu_active"],
        "ac_plugged": record["ac_plugged"],
        "battery_power": record["battery_power"],
        "battery_voltage": record["battery_voltage"],
        "battery_level": record["battery_level"],
    }
    latest_reading[device_id] = res_data
    return res_data

@app.get("/", response_class=HTMLResponse)
def read_root():
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="index.html not found")
