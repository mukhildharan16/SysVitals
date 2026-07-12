"""
SysVitals — FastAPI Backend
"""

import time
import sys
import os
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Add current directory to path to allow running from both root and subfolders
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from .database import query_supabase
    from .models import UserAuth, DeviceRegister, TelemetryIngest
except ImportError:
    from database import query_supabase
    from models import UserAuth, DeviceRegister, TelemetryIngest
import bcrypt

app = FastAPI(title="SysVitals")

# Configure CORS Middleware
# In production, replace ["*"] with your specific production domains (e.g. ["https://sysvitals.pages.dev"])
ALLOWED_ORIGINS = [
    "*",
    "http://localhost:8000",
    "http://127.0.0.1:8000"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
