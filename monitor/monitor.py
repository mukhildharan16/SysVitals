import os
import subprocess
import sys
import time
from pathlib import Path

import requests

# Load monitor-specific configuration without overriding process environment.
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
                os.environ.setdefault(key, val)


def normalize_server_url(value: str) -> str:
    """Return a usable HTTP(S) base URL from a hostname or URL."""
    url = value.strip()
    if url and not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url.rstrip("/")


SERVER_URL = normalize_server_url(os.environ.get("TW_SERVER_URL", ""))
DEVICE_SECRET = os.environ.get("TW_DEVICE_SECRET", "")
INTERVAL = float(os.environ.get("TW_INTERVAL_SECONDS", "10.0"))


IS_WINDOWS = sys.platform == "win32"
_computer = None


def init_windows_sensors():
    global _computer
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        if not is_admin:
            print("Note: Script is not running as Administrator. CPU temperature and other vitals will be fallback/0.0 values.", file=sys.stderr)
            print("To read actual metrics via PawnIO/LibreHardwareMonitor, please run this script in an elevated (Administrator) command prompt/PowerShell.", file=sys.stderr)
        
        from pythonnet import load
        try:
            load("coreclr")
        except RuntimeError:
            pass  # Already loaded
        import clr
        dll_path = r"C:\Softwares\LenovoLegionToolkit\LibreHardwareMonitorLib.dll"
        if os.path.exists(dll_path):
            clr.AddReference(dll_path)
            from LibreHardwareMonitor.Hardware import Computer
            _computer = Computer()
            _computer.IsCpuEnabled = True
            _computer.IsBatteryEnabled = True
            _computer.Open()
        else:
            print(f"Warning: LibreHardwareMonitorLib.dll not found at {dll_path}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to initialize Windows sensors: {e}", file=sys.stderr)


def get_power_status_vitals() -> dict:
    status = {"ac_plugged": None, "battery_level": None}
    if IS_WINDOWS:
        try:
            import ctypes
            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ('ACLineStatus', ctypes.c_byte),
                    ('BatteryFlag', ctypes.c_byte),
                    ('BatteryLifePercent', ctypes.c_byte),
                    ('Reserved1', ctypes.c_byte),
                    ('BatteryLifeTime', ctypes.c_ulong),
                    ('BatteryFullLifeTime', ctypes.c_ulong),
                ]
            s = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
                status["ac_plugged"] = s.ACLineStatus == 1
                if s.BatteryLifePercent <= 100:
                    status["battery_level"] = float(s.BatteryLifePercent)
        except Exception:
            pass
    else:
        # Linux AC power check
        try:
            for ac_name in ["AC", "ACAD", "ADP0"]:
                ac_path = f"/sys/class/power_supply/{ac_name}/online"
                if os.path.exists(ac_path):
                    with open(ac_path) as f:
                        status["ac_plugged"] = f.read().strip() == "1"
                        break
        except Exception:
            pass
    return status


def get_linux_battery_power() -> dict:
    vitals = {"battery_power": None, "battery_level": None, "battery_voltage": None}
    try:
        base = "/sys/class/power_supply"
        if os.path.exists(base):
            for bat in os.listdir(base):
                if bat.startswith("BAT"):
                    cap_path = f"{base}/{bat}/capacity"
                    volt_path = f"{base}/{bat}/voltage_now"
                    power_path = f"{base}/{bat}/power_now"
                    current_path = f"{base}/{bat}/current_now"
                    
                    if os.path.exists(cap_path):
                        with open(cap_path) as f:
                            vitals["battery_level"] = float(f.read().strip())
                    
                    voltage = None
                    if os.path.exists(volt_path):
                        with open(volt_path) as f:
                            voltage = float(f.read().strip()) / 1000000.0 # microvolts to volts
                            vitals["battery_voltage"] = voltage
                    
                    if os.path.exists(power_path):
                        with open(power_path) as f:
                            vitals["battery_power"] = float(f.read().strip()) / 1000000.0 # microwatts to watts
                    elif os.path.exists(current_path) and voltage is not None:
                        with open(current_path) as f:
                            current = float(f.read().strip()) / 1000000.0 # microamperes to amperes
                            vitals["battery_power"] = current * voltage
                    break
    except Exception:
        pass
    return vitals


def get_vitals_from_wmi() -> dict:
    vitals = {
        "cpu_temp": None,
        "cpu_power": None,
        "cpu_clock": None,
        "cpu_util": None
    }

    # Try querying root\LibreHardwareMonitor or root\OpenHardwareMonitor via PowerShell
    for ns in ["root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"]:
        try:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                f"Get-CimInstance -Namespace {ns} -ClassName Sensor | Select-Object Name, SensorType, Value | ConvertTo-Json"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
            if res.returncode == 0 and res.stdout.strip():
                import json
                data = json.loads(res.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                
                for item in data:
                    name = item.get("Name", "")
                    s_type = item.get("SensorType", "")
                    val = item.get("Value")
                    if val is not None:
                        val = float(val)
                        name_upper = name.upper()
                        
                        # CPU sensors mapping (skip any GPU metrics)
                        is_igpu = any(x in name_upper for x in ["GPU", "GFX", "GRAPHICS", "APU"])
                        if not is_igpu:
                            if s_type == "Temperature":
                                if "TCTL" in name_upper or "PACKAGE" in name_upper or vitals["cpu_temp"] is None:
                                    vitals["cpu_temp"] = val
                            elif s_type == "Power" and "PACKAGE" in name_upper:
                                vitals["cpu_power"] = val
                            elif s_type == "Clock" and ("CORES" in name_upper or "CORE" in name_upper or "AVERAGE" in name_upper):
                                vitals["cpu_clock"] = val
                            elif s_type == "Load" and "TOTAL" in name_upper:
                                vitals["cpu_util"] = val
                
                if vitals["cpu_temp"] is not None:
                    break
        except Exception:
            pass

    return vitals


def get_vitals() -> dict:
    vitals = {
        "cpu_temp": None,
        "cpu_power": None,
        "cpu_clock": None,
        "cpu_util": None,
        "battery_power": None,
        "battery_voltage": None,
        "battery_level": None
    }

    if IS_WINDOWS:
        if _computer is not None:
            try:
                # 1) Structured update and primary pass
                for hardware in _computer.Hardware:
                    hardware.Update()
                    h_type = str(hardware.HardwareType)
                    
                    # Primary CPU mapping
                    if "Cpu" in h_type:
                        for sensor in hardware.Sensors:
                            s_type = str(sensor.SensorType)
                            s_name = sensor.Name
                            val = sensor.Value
                            if val is not None:
                                val = float(val)
                                s_name_upper = s_name.upper()
                                is_igpu_sensor = any(x in s_name_upper for x in ["GPU", "GFX", "GRAPHICS", "APU"])
                                
                                if not is_igpu_sensor:
                                    if s_type == "Temperature":
                                        if "Tctl" in s_name or "Package" in s_name or vitals["cpu_temp"] is None:
                                            vitals["cpu_temp"] = val
                                    elif s_type == "Power" and "Package" in s_name:
                                        vitals["cpu_power"] = val
                                    elif s_type == "Clock" and "Average" in s_name:
                                        vitals["cpu_clock"] = val
                                    elif s_type == "Load" and "Total" in s_name:
                                        vitals["cpu_util"] = val
                    elif "Battery" in h_type:
                        for sensor in hardware.Sensors:
                            s_type = str(sensor.SensorType)
                            s_name = sensor.Name
                            val = sensor.Value
                            if val is not None:
                                val = float(val)
                                if s_type == "Power" and "Rate" in s_name:
                                    vitals["battery_power"] = val
                                elif s_type == "Voltage":
                                    vitals["battery_voltage"] = val
                                elif s_type == "Level" and "Charge" in s_name:
                                    vitals["battery_level"] = val

                # 2) Secondary Pass: Global Name-based fallback across ALL hardware types (SuperIO, Motherboard, etc.)
                for hardware in _computer.Hardware:
                    for sensor in hardware.Sensors:
                        s_type = str(sensor.SensorType)
                        s_name = sensor.Name
                        val = sensor.Value
                        if val is not None:
                            val = float(val)
                            s_name_upper = s_name.upper()
                            
                            is_igpu_name = any(x in s_name_upper for x in ["GPU", "GFX", "GRAPHICS", "APU"])
                            if not is_igpu_name:
                                if s_type == "Temperature" and vitals["cpu_temp"] is None:
                                    if "CPU" in s_name_upper or "CORE" in s_name_upper or "PACKAGE" in s_name_upper:
                                        vitals["cpu_temp"] = val
                                elif s_type == "Power" and vitals["cpu_power"] is None:
                                    if "CPU" in s_name_upper or "PACKAGE" in s_name_upper:
                                        vitals["cpu_power"] = val
                                elif s_type == "Clock" and vitals["cpu_clock"] is None:
                                    if "CPU" in s_name_upper:
                                        vitals["cpu_clock"] = val
                                elif s_type == "Load" and vitals["cpu_util"] is None:
                                    if "CPU" in s_name_upper:
                                        vitals["cpu_util"] = val

            except Exception as e:
                print(f"Error reading vitals on Windows: {e}", file=sys.stderr)

        # 3) Last resort WMI fallback
        if vitals["cpu_temp"] is None or vitals["cpu_temp"] == 0.0:
            wmi_vitals = get_vitals_from_wmi()
            if wmi_vitals["cpu_temp"] is not None:
                for k, v in wmi_vitals.items():
                    if v is not None:
                        vitals[k] = v

        return vitals

    # Linux implementation (fallback/default values)
    # CPU Temp
    try:
        import psutil

        temps = psutil.sensors_temperatures()
        for key in ("k10temp", "coretemp", "cpu_thermal", "zenpower"):
            if key in temps and temps[key]:
                vitals["cpu_temp"] = float(temps[key][0].current)
                break
        if vitals["cpu_temp"] is None:
            for entries in temps.values():
                if entries:
                    vitals["cpu_temp"] = float(entries[0].current)
                    break
    except Exception:
        pass

    if vitals["cpu_temp"] is None:
        try:
            base = "/sys/class/thermal"
            for zone in sorted(os.listdir(base)):
                type_path = f"{base}/{zone}/type"
                temp_path = f"{base}/{zone}/temp"
                if os.path.exists(temp_path):
                    with open(type_path) as f:
                        zone_type = f.read().strip().lower()
                    if "cpu" in zone_type or "x86_pkg_temp" in zone_type or "k10temp" in zone_type:
                        with open(temp_path) as f:
                            vitals["cpu_temp"] = int(f.read().strip()) / 1000.0
                            break
        except Exception:
            pass

    # CPU Util
    try:
        import psutil

        vitals["cpu_util"] = float(psutil.cpu_percent())
    except Exception:
        pass

    # CPU Clock
    try:
        import psutil

        vitals["cpu_clock"] = float(psutil.cpu_freq().current)
    except Exception:
        pass

    # Fallbacks for Linux
    vitals["cpu_power"] = 0.0

    vitals.update(get_linux_battery_power())

    return vitals


class GpuMonitor:
    def __init__(self):
        self.initialized = False
        self.handle = None
        self.last_vitals = {
            "gpu_name": None,
            "gpu_temp": None,
            "gpu_power": None,
            "gpu_util": None,
            "gpu_mem_used": None,
            "gpu_mem_total": None,
            "gpu_active": None
        }
        try:
            import pynvml
            pynvml.nvmlInit()
            self.initialized = True
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            
            # Fetch name once
            try:
                name = pynvml.nvmlDeviceGetName(self.handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8")
                self.last_vitals["gpu_name"] = name
            except Exception:
                self.last_vitals["gpu_name"] = "NVIDIA GPU"
            self.last_vitals["gpu_active"] = True
        except Exception as e:
            print(f"Note: NVML could not be initialized (no NVIDIA GPU or driver): {e}", file=sys.stderr)
            self.last_vitals["gpu_active"] = False

    def get_gpu_vitals(self) -> dict:
        if not self.initialized or not self.handle:
            self.last_vitals["gpu_active"] = False
            return self.last_vitals
            
        import pynvml
        gpu_active = True
        
        try:
            # Check temperature as an indicator of sleep state
            temp = None
            try:
                temp_val = pynvml.nvmlDeviceGetTemperature(self.handle, pynvml.NVML_TEMPERATURE_GPU)
                if temp_val > 0:
                    temp = float(temp_val)
                else:
                    gpu_active = False
            except pynvml.NVMLError:
                gpu_active = False

            if gpu_active:
                self.last_vitals["gpu_active"] = True
                if temp is not None:
                    self.last_vitals["gpu_temp"] = temp
                
                # Power Draw (mW to W)
                try:
                    power_mw = pynvml.nvmlDeviceGetPowerUsage(self.handle)
                    self.last_vitals["gpu_power"] = float(power_mw) / 1000.0
                except pynvml.NVMLError:
                    pass
                    
                # Utilization
                try:
                    util = pynvml.nvmlDeviceGetUtilizationRates(self.handle)
                    self.last_vitals["gpu_util"] = float(util.gpu)
                except pynvml.NVMLError:
                    pass
                    
                # Memory Info (Bytes to MiB)
                try:
                    mem = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
                    self.last_vitals["gpu_mem_used"] = float(mem.used) / (1024 * 1024)
                    self.last_vitals["gpu_mem_total"] = float(mem.total) / (1024 * 1024)
                except pynvml.NVMLError:
                    pass
            else:
                self.last_vitals["gpu_active"] = False
                # If GPU is sleeping, try to read power draw (which NVML sometimes still reports)
                # but keep previous valid temperature, utilization, and memory info
                try:
                    power_mw = pynvml.nvmlDeviceGetPowerUsage(self.handle)
                    self.last_vitals["gpu_power"] = float(power_mw) / 1000.0
                except pynvml.NVMLError:
                    pass
                    
        except Exception as e:
            print(f"Warning: Exception while polling GPU vitals: {e}", file=sys.stderr)
            
        return self.last_vitals

    def shutdown(self):
        if self.initialized:
            try:
                import pynvml
                pynvml.nvmlShutdown()
                self.initialized = False
            except Exception:
                pass



def get_power_mode() -> str:
    if IS_WINDOWS:
        # Retrieve exclusively from Lenovo Legion Toolkit CLI (llt.exe)
        llt_path = r"C:\Softwares\LenovoLegionToolkit\llt.exe"
        if os.path.exists(llt_path):
            try:
                out = subprocess.run(
                    [llt_path, "feature", "get", "power-mode"],
                    capture_output=True, text=True, timeout=3
                )
                if out.returncode == 0:
                    val = out.stdout.strip().lower()
                    if val:
                        mapping = {
                            "quiet": "quiet",
                            "balanced": "balanced",
                            "performance": "performance",
                            "custom": "turbo"
                        }
                        return mapping.get(val, val)
                else:
                    print("Warning: llt.exe failed to get power-mode. Ensure CLI is enabled in Lenovo Legion Toolkit settings.", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Failed to execute Lenovo Legion Toolkit CLI: {e}", file=sys.stderr)
        else:
            print(f"Warning: llt.exe not found at {llt_path}", file=sys.stderr)
        return "unknown"

    # Linux implementation
    profile = None
    try:
        out = subprocess.run(
            ["powerprofilesctl", "get"], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0:
            profile = out.stdout.strip()
    except Exception:
        pass

    label = {
        "power-saver": "quiet",
        "balanced": "balanced",
        "performance": "performance",
    }.get(profile, profile or "unknown")

    if label == "performance":
        try:
            with open("/sys/devices/system/cpu/cpufreq/boost") as f:
                if f.read().strip() == "1":
                    label = "turbo"
        except Exception:
            pass

    return label


def send_reading(vitals: dict, power_mode: str):
    payload = {
        "device_secret": DEVICE_SECRET,
        "cpu_temp": vitals.get("cpu_temp"),
        "cpu_power": vitals.get("cpu_power"),
        "cpu_clock": vitals.get("cpu_clock"),
        "cpu_util": vitals.get("cpu_util"),
        "gpu_name": vitals.get("gpu_name"),
        "gpu_temp": vitals.get("gpu_temp"),
        "gpu_power": vitals.get("gpu_power"),
        "gpu_util": vitals.get("gpu_util"),
        "gpu_mem_used": vitals.get("gpu_mem_used"),
        "gpu_mem_total": vitals.get("gpu_mem_total"),
        "gpu_active": vitals.get("gpu_active"),
        "ac_plugged": vitals.get("ac_plugged"),
        "battery_power": vitals.get("battery_power"),
        "battery_voltage": vitals.get("battery_voltage"),
        "battery_level": vitals.get("battery_level"),
        "power_mode": power_mode
    }
    
    resp = requests.post(f"{SERVER_URL}/api/ingest", json=payload, timeout=10)
    resp.raise_for_status()


def main():
    if not SERVER_URL or not DEVICE_SECRET:
        print("Set TW_SERVER_URL and TW_DEVICE_SECRET environment variables first.", file=sys.stderr)
        sys.exit(1)

    init_windows_sensors()
    gpu_monitor = GpuMonitor()

    print(f"SysVitals monitor started. Reporting to {SERVER_URL} every {INTERVAL}s.")
    try:
        while True:
            vitals = get_vitals()
            gpu_vitals = gpu_monitor.get_gpu_vitals()
            vitals.update(gpu_vitals)
            
            p_status = get_power_status_vitals()
            for k, v in p_status.items():
                if vitals.get(k) is None:
                    vitals[k] = v

            mode = get_power_mode()
            try:
                send_reading(vitals, mode)
                cpu_t = f"{vitals['cpu_temp']:.1f}°C" if vitals['cpu_temp'] is not None else "N/A"
                cpu_u = f"{vitals['cpu_util']:.0f}%" if vitals['cpu_util'] is not None else "N/A"
                cpu_p = f"{vitals['cpu_power']:.1f}W" if vitals['cpu_power'] is not None else "N/A"
                cpu_c = f"{vitals['cpu_clock']:.0f}MHz" if vitals['cpu_clock'] is not None else "N/A"

                gpu_t = f"{vitals['gpu_temp']:.1f}°C" if vitals.get('gpu_temp') is not None else "N/A"
                gpu_u = f"{vitals['gpu_util']:.0f}%" if vitals.get('gpu_util') is not None else "N/A"
                gpu_p = f"{vitals['gpu_power']:.1f}W" if vitals.get('gpu_power') is not None else "N/A"
                gpu_m = f"{vitals['gpu_mem_used']:.0f}/{vitals['gpu_mem_total']:.0f}MiB" if vitals.get('gpu_mem_used') is not None and vitals.get('gpu_mem_total') is not None else "N/A"

                p_source = "AC" if vitals.get("ac_plugged") else "BAT"
                bat_pct = f"{vitals.get('battery_level'):.0f}%" if vitals.get('battery_level') is not None else "N/A"
                bat_p = f"{vitals.get('battery_power'):.1f}W" if vitals.get('battery_power') is not None else "N/A"

                print(
                    f"[{time.strftime('%H:%M:%S')}] "
                    f"CPU: {cpu_t} ({cpu_u}, {cpu_p}, {cpu_c}) | "
                    f"GPU: {gpu_t} ({gpu_u}, {gpu_p}, {gpu_m}) | "
                    f"Power: {p_source} ({bat_pct}, {bat_p}) | "
                    f"mode={mode} -> sent"
                )
            except Exception as e:
                print(f"Failed to send reading: {e}", file=sys.stderr)
            time.sleep(INTERVAL)
    finally:
        gpu_monitor.shutdown()
        if IS_WINDOWS and _computer is not None:
            try:
                _computer.Close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
