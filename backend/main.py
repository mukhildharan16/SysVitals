"""SysVitals FastAPI backend."""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import bcrypt
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .database import (
    create_device,
    create_access_token,
    create_user,
    get_device,
    get_device_by_secret,
    get_latest_telemetry,
    get_telemetry_history,
    get_user_by_username,
    get_user_by_access_token,
    get_user_devices,
    initialize_database,
    save_telemetry,
)
from .models import DeviceRegister, TelemetryIngest, UserAuth


logger = logging.getLogger("sysvitals")


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    logger.info("SysVitals database initialized")
    yield


app = FastAPI(title="SysVitals", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


# Recent values avoid a database read on every dashboard poll. SQLite remains the
# source of truth and is used after a process restart.
latest_reading: dict[str, dict] = {}


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """Authenticate dashboard and desktop API calls with a bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    user = get_user_by_access_token(authorization.removeprefix("Bearer ").strip())
    if not user:
        raise HTTPException(status_code=401, detail="Invalid access token")
    return user


def get_owned_device(device_id: str, user: dict) -> dict:
    device = get_device(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@app.post("/api/register")
def register(auth: UserAuth):
    password_hash = bcrypt.hashpw(
        auth.password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    user_id = create_user(auth.username, password_hash, _utc_now())
    if not user_id:
        raise HTTPException(status_code=400, detail="Username already exists")
    return {"success": True, "message": "User registered successfully"}


@app.post("/api/login")
def login(auth: UserAuth):
    user = get_user_by_username(auth.username)
    if not user or not bcrypt.checkpw(
        auth.password.encode("utf-8"), user["password_hash"].encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {
        "success": True,
        "user_id": user["id"],
        "access_token": create_access_token(user["id"], _utc_now()),
    }


@app.post("/api/device/register")
def register_device(device: DeviceRegister, user: dict = Depends(get_current_user)):
    if device.user_id != user["id"]:
        raise HTTPException(status_code=403, detail="Cannot register a device for another user")
    device_secret = create_device(
        device.user_id, device.device_name, device.hostname, _utc_now()
    )
    if not device_secret:
        raise HTTPException(status_code=400, detail="Invalid user")
    return {"device_secret": device_secret}


@app.get("/api/user/{user_id}/devices")
def list_user_devices(user_id: str, user: dict = Depends(get_current_user)):
    if user_id != user["id"]:
        raise HTTPException(status_code=403, detail="Cannot view another user's devices")
    return get_user_devices(user_id)


@app.post("/api/ingest")
def ingest(payload: TelemetryIngest):
    device = get_device_by_secret(payload.device_secret)
    if not device:
        raise HTTPException(status_code=401, detail="Invalid device secret")

    device_id = device["id"]
    timestamp = _utc_now()
    telemetry = payload.model_dump(exclude={"device_secret"})
    save_telemetry(device_id, timestamp, telemetry)

    latest_reading[device_id] = {
        "ts": time.time(),
        "hostname": device.get("hostname") or device["name"],
        **telemetry,
    }
    return {"status": "ok"}


@app.get("/api/device/{device_id}/latest")
def get_device_latest(device_id: str, user: dict = Depends(get_current_user)):
    get_owned_device(device_id, user)
    if device_id in latest_reading:
        return latest_reading[device_id]

    record = get_latest_telemetry(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="No telemetry data found for this device")

    try:
        timestamp = datetime.fromisoformat(record["ts"]).timestamp()
    except (TypeError, ValueError):
        timestamp = time.time()

    result = {
        "ts": timestamp,
        **{
            key: record[key]
            for key in (
                "cpu_name", "cpu_temp", "power_mode", "cpu_power", "cpu_clock", "cpu_util",
                "gpu_name", "gpu_temp", "gpu_power", "gpu_util", "gpu_mem_used",
                "gpu_mem_total", "gpu_active", "ac_plugged", "battery_power",
                "battery_voltage", "battery_level", "memory_used_mb",
                "applications_open", "uptime_seconds", "current_user",
            )
        },
    }
    for boolean_field in ("gpu_active", "ac_plugged"):
        if result[boolean_field] is not None:
            result[boolean_field] = bool(result[boolean_field])
    latest_reading[device_id] = result
    return result


@app.get("/api/device/{device_id}/telemetry.json")
def get_device_telemetry_json(
    device_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    user: dict = Depends(get_current_user),
):
    """Expose recent raw monitor readings as a JSON document for integrations."""
    get_owned_device(device_id, user)
    readings = get_telemetry_history(device_id, limit)
    if not readings:
        raise HTTPException(status_code=404, detail="No telemetry data found for this device")
    return {"device_id": device_id, "count": len(readings), "readings": readings}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
