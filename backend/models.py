from pydantic import BaseModel


class UserAuth(BaseModel):
    username: str
    password: str


class DeviceRegister(BaseModel):
    user_id: str
    device_name: str
    hostname: str | None = None


class TelemetryIngest(BaseModel):
    device_secret: str
    cpu_temp: float | None = None
    cpu_power: float | None = None
    cpu_clock: float | None = None
    cpu_util: float | None = None
    gpu_name: str | None = None
    gpu_temp: float | None = None
    gpu_power: float | None = None
    gpu_util: float | None = None
    gpu_mem_used: float | None = None
    gpu_mem_total: float | None = None
    gpu_active: bool | None = None
    ac_plugged: bool | None = None
    battery_power: float | None = None
    battery_voltage: float | None = None
    battery_level: float | None = None
    memory_used_mb: float | None = None
    applications_open: list[str] | None = None
    uptime_seconds: float | None = None
    current_user: str | None = None
    power_mode: str
