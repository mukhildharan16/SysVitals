import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Optional

# 1. Zero-dependency .env loader
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
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                os.environ.setdefault(key, val)

# 2. Third-party HTTP requests import
try:
    import requests
except ImportError:
    print("Error: 'requests' package not installed. Run 'pip install -r requirements.txt' first.", file=sys.stderr)
    sys.exit(1)

# Env-based config
SERVER_URL = os.environ.get("TW_SERVER_URL", "")
API_KEY = os.environ.get("TW_API_KEY", "")
INTERVAL = float(os.environ.get("TW_INTERVAL_SECONDS", "10.0"))

# Retrieve default hostname
try:
    import socket
    HOSTNAME = socket.gethostname()
except Exception:
    HOSTNAME = "unknown-client"

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
            import LibreHardwareMonitor
            from LibreHardwareMonitor.Hardware import Computer
            _computer = Computer()
            _computer.IsCpuEnabled = True
            _computer.Open()
        else:
            print(f"Warning: LibreHardwareMonitorLib.dll not found at {dll_path}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to initialize Windows sensors: {e}", file=sys.stderr)


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
        "cpu_util": None
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

    return vitals


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
        "cpu_temp": vitals.get("cpu_temp"),
        "cpu_power": vitals.get("cpu_power"),
        "cpu_clock": vitals.get("cpu_clock"),
        "cpu_util": vitals.get("cpu_util"),
        "power_mode": power_mode,
        "hostname": HOSTNAME
    }
    headers = {"X-API-Key": API_KEY}
    
    errors = []
    # 1) Try sending to configured SERVER_URL
    if SERVER_URL:
        try:
            resp = requests.post(f"{SERVER_URL}/api/ingest", json=payload, headers=headers, timeout=5)
            resp.raise_for_status()
            return
        except Exception as e:
            errors.append(f"Configured server ({SERVER_URL}): {e}")

    # 2) Fallback to local server if SERVER_URL is not local
    local_url = "http://127.0.0.1:8000"
    if SERVER_URL != local_url:
        try:
            resp = requests.post(f"{local_url}/api/ingest", json=payload, headers=headers, timeout=2)
            resp.raise_for_status()
            return
        except Exception as e:
            errors.append(f"Local fallback ({local_url}): {e}")

    raise Exception(" -> ".join(errors))


def main():
    if not SERVER_URL or not API_KEY:
        print("Set TW_SERVER_URL and TW_API_KEY environment variables first.", file=sys.stderr)
        sys.exit(1)

    init_windows_sensors()

    print(f"Thermal Watch client started. Reporting to {SERVER_URL} every {INTERVAL}s as '{HOSTNAME}'.")
    try:
        while True:
            vitals = get_vitals()
            mode = get_power_mode()
            try:
                send_reading(vitals, mode)
                cpu_t = f"{vitals['cpu_temp']:.1f}°C" if vitals['cpu_temp'] is not None else "N/A"
                cpu_u = f"{vitals['cpu_util']:.0f}%" if vitals['cpu_util'] is not None else "N/A"
                cpu_p = f"{vitals['cpu_power']:.1f}W" if vitals['cpu_power'] is not None else "N/A"
                cpu_c = f"{vitals['cpu_clock']:.0f}MHz" if vitals['cpu_clock'] is not None else "N/A"

                print(
                    f"[{time.strftime('%H:%M:%S')}] "
                    f"CPU: {cpu_t} ({cpu_u}, {cpu_p}, {cpu_c}) | "
                    f"mode={mode} -> sent"
                )
            except Exception as e:
                print(f"Failed to send reading: {e}", file=sys.stderr)
            time.sleep(INTERVAL)
    finally:
        if IS_WINDOWS and _computer is not None:
            try:
                _computer.Close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
