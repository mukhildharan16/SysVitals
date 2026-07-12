from pydantic import BaseModel
from typing import Optional

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
