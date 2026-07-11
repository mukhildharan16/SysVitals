"""
SysVitals — backend
------------------------
Receives CPU + iGPU telemetry from the client script and serves the live dashboard at /.
No data collection or database storage is performed.
"""

import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
                os.environ.setdefault(key, val)

API_KEY = os.environ.get("INGEST_API_KEY", "")

app = FastAPI(title="Thermal Watch")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Reading(BaseModel):
    cpu_temp: Optional[float] = Field(None, description="CPU temperature in Celsius")
    power_mode: str = Field(..., description="e.g. quiet / balanced / performance / turbo")
    hostname: Optional[str] = Field(None, description="Machine identifier, optional")
    
    cpu_power: Optional[float] = Field(None, description="CPU power draw in Watts")
    cpu_clock: Optional[float] = Field(None, description="CPU clock speed in MHz")
    cpu_util: Optional[float] = Field(None, description="CPU utilization in %")


# In-memory storage for the latest reading
latest_reading = {}


def require_api_key(x_api_key: Optional[str]):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Server has no INGEST_API_KEY configured")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.post("/api/ingest")
def ingest(reading: Reading, x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    global latest_reading
    latest_reading = {
        "ts": time.time(),
        "hostname": reading.hostname,
        "cpu_temp": reading.cpu_temp,
        "power_mode": reading.power_mode,
        "cpu_power": reading.cpu_power,
        "cpu_clock": reading.cpu_clock,
        "cpu_util": reading.cpu_util,
    }
    return {"status": "ok"}


@app.get("/api/latest")
def latest():
    global latest_reading
    if not latest_reading:
        raise HTTPException(status_code=404, detail="No readings yet")
    return latest_reading


app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
