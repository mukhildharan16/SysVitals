import os
import getpass
import platform
import shutil
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
HTTP_CONNECT_TIMEOUT = float(os.environ.get("TW_HTTP_CONNECT_TIMEOUT", "3.0"))
HTTP_READ_TIMEOUT = float(os.environ.get("TW_HTTP_READ_TIMEOUT", "10.0"))

# Keep one outbound connection open.  Recreating a TLS connection on every
# report can be very slow on networks where an unusable address family must
# time out before Cloudflare's reachable endpoint is tried.
HTTP_SESSION = requests.Session()


OS_NAME = platform.system()
IS_WINDOWS = OS_NAME == "Windows"
IS_LINUX = OS_NAME == "Linux"
_computer = None
_cpu_model: str | None = None
_cpu_model_loaded = False
_rapl_energy_samples: dict[str, tuple[float, float]] = {}
_host_details: dict | None = None


def set_metric_error(vitals: dict, metric: str, reason: str) -> None:
    """Expose an unavailable metric to the API/dashboard instead of hiding it."""
    vitals.setdefault("metric_errors", {}).setdefault(metric, reason)


def read_text(path: str, *, metric: str | None = None, vitals: dict | None = None) -> str | None:
    """Read a sysfs/proc file and retain a useful error when it is inaccessible."""
    try:
        with open(path, encoding="utf-8") as file:
            return file.read().strip()
    except OSError as error:
        if metric and vitals is not None:
            set_metric_error(vitals, metric, f"cannot read {path}: {error.strerror or error}")
        return None


def get_host_details() -> dict:
    """Detect the operating system and DMI manufacturer/model once per run."""
    global _host_details
    if _host_details is not None:
        return _host_details.copy()

    details = {
        "os_name": OS_NAME or "Unknown OS",
        "os_version": platform.platform() or None,
        "manufacturer": None,
        "model": None,
        "metric_errors": {},
    }
    if IS_LINUX:
        os_release = {}
        for line in (read_text("/etc/os-release") or "").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os_release[key] = value.strip().strip('"')
        details["os_version"] = os_release.get("PRETTY_NAME", details["os_version"])
        details["manufacturer"] = read_text("/sys/class/dmi/id/sys_vendor")
        details["model"] = read_text("/sys/class/dmi/id/product_name")
        if not details["manufacturer"]:
            set_metric_error(details, "manufacturer", "Linux DMI vendor data is not available")
        if not details["model"]:
            set_metric_error(details, "model", "Linux DMI product data is not available")
    elif IS_WINDOWS:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model | ConvertTo-Json -Compress)"],
                capture_output=True, text=True, timeout=4,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                computer = json.loads(result.stdout)
                details["manufacturer"] = computer.get("Manufacturer") or None
                details["model"] = computer.get("Model") or None
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            set_metric_error(details, "manufacturer", f"cannot read Windows system manufacturer: {error}")
            set_metric_error(details, "model", f"cannot read Windows system model: {error}")
        if not details["manufacturer"]:
            set_metric_error(details, "manufacturer", "Windows did not report a system manufacturer")
        if not details["model"]:
            set_metric_error(details, "model", "Windows did not report a system model")
    _host_details = details
    return details.copy()


def is_lenovo_host() -> bool:
    return "lenovo" in (get_host_details().get("manufacturer") or "").casefold()


def get_cpu_model() -> str | None:
    """Return the processor marketing name once; it does not change at runtime."""
    global _cpu_model, _cpu_model_loaded
    if _cpu_model_loaded:
        return _cpu_model

    _cpu_model_loaded = True
    try:
        if IS_WINDOWS:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                _cpu_model = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        elif os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
                for line in cpuinfo:
                    if line.lower().startswith("model name") and ":" in line:
                        _cpu_model = line.split(":", 1)[1].strip()
                        break
    except (OSError, ValueError):
        pass

    if not _cpu_model:
        _cpu_model = platform.processor().strip() or None
    return _cpu_model


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
    status = {"ac_plugged": None, "battery_level": None, "metric_errors": {}}
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
        except Exception as error:
            set_metric_error(status, "ac_plugged", f"Windows power status unavailable: {error}")
    else:
        # Linux kernels expose AC adapters by type rather than a fixed name.
        try:
            base = "/sys/class/power_supply"
            for name in os.listdir(base):
                type_name = read_text(f"{base}/{name}/type")
                online = f"{base}/{name}/online"
                if type_name and type_name.casefold() in {"mains", "usb", "usb_c"} and os.path.exists(online):
                    raw = read_text(online, metric="ac_plugged", vitals=status)
                    if raw is not None:
                        status["ac_plugged"] = raw == "1"
                        break
            if status["ac_plugged"] is None:
                set_metric_error(status, "ac_plugged", "no AC adapter was exposed by /sys/class/power_supply")
        except OSError as error:
            set_metric_error(status, "ac_plugged", f"cannot inspect Linux power supplies: {error}")
    return status


def get_linux_battery_power() -> dict:
    vitals = {"battery_power": None, "battery_level": None, "battery_voltage": None, "metric_errors": {}}
    try:
        base = "/sys/class/power_supply"
        if os.path.exists(base):
            for bat in os.listdir(base):
                if (read_text(f"{base}/{bat}/type") or "").casefold() == "battery":
                    cap_path = f"{base}/{bat}/capacity"
                    volt_path = f"{base}/{bat}/voltage_now"
                    power_path = f"{base}/{bat}/power_now"
                    current_path = f"{base}/{bat}/current_now"
                    
                    if os.path.exists(cap_path):
                        value = read_text(cap_path, metric="battery_level", vitals=vitals)
                        if value is not None:
                            vitals["battery_level"] = float(value)
                    
                    voltage = None
                    if os.path.exists(volt_path):
                        value = read_text(volt_path, metric="battery_voltage", vitals=vitals)
                        if value is not None:
                            voltage = float(value) / 1000000.0 # microvolts to volts
                            vitals["battery_voltage"] = voltage
                    
                    if os.path.exists(power_path):
                        value = read_text(power_path, metric="battery_power", vitals=vitals)
                        if value is not None:
                            vitals["battery_power"] = abs(float(value) / 1000000.0) # microwatts to watts
                    elif os.path.exists(current_path) and voltage is not None:
                        value = read_text(current_path, metric="battery_power", vitals=vitals)
                        if value is not None:
                            current = float(value) / 1000000.0 # microamperes to amperes
                            vitals["battery_power"] = current * voltage
                    break
        if vitals["battery_level"] is None:
            set_metric_error(vitals, "battery_level", "no battery capacity sensor was exposed by Linux")
        if vitals["battery_power"] is None:
            set_metric_error(vitals, "battery_power", "battery charge/discharge power is not exposed by this Linux battery driver")
        if vitals["battery_voltage"] is None:
            set_metric_error(vitals, "battery_voltage", "battery voltage is not exposed by this Linux battery driver")
    except (OSError, ValueError) as error:
        set_metric_error(vitals, "battery_level", f"cannot read Linux battery data: {error}")
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


def get_linux_cpu_power(vitals: dict) -> float | None:
    """Read package power from Intel/AMD RAPL energy counters when available."""
    now = time.monotonic()
    try:
        domains = []
        for root, _, files in os.walk("/sys/class/powercap"):
            if "energy_uj" not in files:
                continue
            name = (read_text(os.path.join(root, "name")) or "").casefold()
            if "package" in name or "pkg" in name:
                domains.append(root)
        if not domains:
            set_metric_error(vitals, "cpu_power", "RAPL package-energy counters are not available (load the CPU powercap driver if supported)")
            return None

        watts = []
        for domain in domains:
            energy_text = read_text(os.path.join(domain, "energy_uj"), metric="cpu_power", vitals=vitals)
            if energy_text is None:
                continue
            energy = float(energy_text)
            previous = _rapl_energy_samples.get(domain)
            _rapl_energy_samples[domain] = (energy, now)
            if previous:
                delta_energy = energy - previous[0]
                if delta_energy < 0:  # Counter wrapped.
                    max_range = read_text(os.path.join(domain, "max_energy_range_uj"))
                    if max_range:
                        delta_energy += float(max_range)
                elapsed = now - previous[1]
                if elapsed > 0:
                    watts.append(delta_energy / 1_000_000 / elapsed)
        if watts:
            return sum(watts)
        set_metric_error(vitals, "cpu_power", "waiting for a second RAPL sample to calculate CPU package power")
    except (OSError, ValueError) as error:
        set_metric_error(vitals, "cpu_power", f"cannot read Linux CPU package power: {error}")
    return None


def collect_linux_cpu_vitals(vitals: dict) -> None:
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        preferred = ("k10temp", "coretemp", "cpu_thermal", "zenpower")
        for key in preferred:
            if temps.get(key):
                vitals["cpu_temp"] = float(temps[key][0].current)
                break
        if vitals["cpu_temp"] is None:
            for entries in temps.values():
                if entries:
                    vitals["cpu_temp"] = float(entries[0].current)
                    break
        vitals["cpu_util"] = float(psutil.cpu_percent(interval=None))
        frequency = psutil.cpu_freq()
        if frequency:
            vitals["cpu_clock"] = float(frequency.current)
        else:
            set_metric_error(vitals, "cpu_clock", "Linux did not expose the current CPU frequency")
    except Exception as error:
        set_metric_error(vitals, "cpu_util", f"cannot read Linux CPU utilization: {error}")
        set_metric_error(vitals, "cpu_clock", f"cannot read Linux CPU frequency: {error}")

    if vitals["cpu_temp"] is None:
        try:
            for zone in sorted(os.listdir("/sys/class/thermal")):
                type_path = f"/sys/class/thermal/{zone}/type"
                temp_path = f"/sys/class/thermal/{zone}/temp"
                zone_type = (read_text(type_path) or "").casefold()
                raw = read_text(temp_path)
                if raw and any(marker in zone_type for marker in ("cpu", "x86_pkg_temp", "k10temp")):
                    vitals["cpu_temp"] = float(raw) / 1000.0
                    break
        except OSError as error:
            set_metric_error(vitals, "cpu_temp", f"cannot inspect Linux thermal zones: {error}")
    if vitals["cpu_temp"] is None:
        set_metric_error(vitals, "cpu_temp", "no CPU temperature sensor is exposed; install/configure lm-sensors if this hardware supports it")
    if vitals["cpu_util"] is None:
        set_metric_error(vitals, "cpu_util", "Linux CPU utilization was unavailable")
    vitals["cpu_power"] = get_linux_cpu_power(vitals)


def get_vitals() -> dict:
    vitals = {
        "cpu_temp": None,
        "cpu_power": None,
        "cpu_clock": None,
        "cpu_util": None,
        "battery_power": None,
        "battery_voltage": None,
        "battery_level": None,
        "metric_errors": {},
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

    if not IS_LINUX:
        for metric in ("cpu_temp", "cpu_power", "cpu_clock", "cpu_util"):
            set_metric_error(vitals, metric, f"{OS_NAME or 'this operating system'} is not supported by this monitor")
        return vitals

    collect_linux_cpu_vitals(vitals)
    merge_vitals(vitals, get_linux_battery_power())

    return vitals


def merge_vitals(target: dict, source: dict) -> None:
    """Merge collector output without losing diagnostics from another collector."""
    errors = source.pop("metric_errors", {})
    target.update(source)
    target.setdefault("metric_errors", {}).update(errors)


def get_system_details() -> dict:
    """Return supplemental system information independent of hardware sensors."""
    details = {
        "cpu_name": get_cpu_model(),
        "memory_used_mb": None,
        "applications_open": None,
        "uptime_seconds": None,
        "current_user": None,
        "metric_errors": {},
    }
    try:
        import psutil

        details["memory_used_mb"] = float(psutil.virtual_memory().used) / (1024 * 1024)
        details["uptime_seconds"] = max(0.0, time.time() - psutil.boot_time())

        applications = set()
        for process in psutil.process_iter(["name"]):
            try:
                name = process.info["name"]
                if name:
                    applications.add(name)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        details["applications_open"] = sorted(applications, key=str.casefold)
    except Exception as error:
        for metric in ("memory_used_mb", "uptime_seconds", "applications_open"):
            set_metric_error(details, metric, f"cannot read system details: {error}")

    try:
        details["current_user"] = getpass.getuser()
    except Exception as error:
        set_metric_error(details, "current_user", f"cannot read current user: {error}")
    return details


class GpuMonitor:
    def __init__(self):
        self.initialized = False
        self.handle = None
        self.linux_gpu_path: str | None = None
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
            if IS_LINUX:
                self._initialize_linux_gpu()

    def _initialize_linux_gpu(self) -> None:
        """Use standard DRM/sysfs telemetry for AMD and Intel when NVML is absent."""
        try:
            for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
                device = card / "device"
                vendor = read_text(str(device / "vendor"))
                if vendor not in {"0x1002", "0x8086"}:
                    continue
                self.linux_gpu_path = str(device)
                self.last_vitals["gpu_name"] = "AMD GPU" if vendor == "0x1002" else "Intel GPU"
                self.last_vitals["gpu_active"] = True
                return
        except OSError as error:
            print(f"Warning: Cannot inspect Linux DRM GPUs: {error}", file=sys.stderr)

    def _get_linux_gpu_vitals(self) -> dict:
        if not self.linux_gpu_path:
            return self.last_vitals
        device = self.linux_gpu_path
        try:
            busy = read_text(f"{device}/gpu_busy_percent")
            if busy is not None:
                self.last_vitals["gpu_util"] = float(busy)
            used = read_text(f"{device}/mem_info_vram_used")
            total = read_text(f"{device}/mem_info_vram_total")
            if used is not None and total is not None:
                self.last_vitals["gpu_mem_used"] = float(used) / (1024 * 1024)
                self.last_vitals["gpu_mem_total"] = float(total) / (1024 * 1024)

            hwmon_root = Path(device) / "hwmon"
            for hwmon in hwmon_root.glob("hwmon*"):
                temperature = read_text(str(hwmon / "temp1_input"))
                power = read_text(str(hwmon / "power1_average"))
                if temperature is not None:
                    self.last_vitals["gpu_temp"] = float(temperature) / 1000.0
                if power is not None:
                    self.last_vitals["gpu_power"] = float(power) / 1_000_000
                if temperature is not None or power is not None:
                    break
        except (OSError, ValueError) as error:
            print(f"Warning: Cannot read Linux DRM GPU telemetry: {error}", file=sys.stderr)
        return self.last_vitals

    def get_gpu_vitals(self) -> dict:
        if not self.initialized or not self.handle:
            if self.linux_gpu_path:
                return self._get_linux_gpu_vitals()
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



def get_power_mode(vitals: dict) -> str:
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
        set_metric_error(vitals, "power_mode", "Lenovo Legion Toolkit could not provide the Windows power mode")
        return "unknown"

    # LenovoLegionLinux publishes the active Legion mode via the standard
    # platform_profile interface. This is read-only, unlike legion_cli's fan
    # curve commands, so collecting telemetry never changes device settings.
    profile_path = "/sys/firmware/acpi/platform_profile"
    profile = read_text(profile_path)
    if profile:
        return {
            "low-power": "quiet",
            "quiet": "quiet",
            "balanced": "balanced",
            "performance": "performance",
            "custom": "turbo",
        }.get(profile.casefold(), profile.casefold())

    try:
        out = subprocess.run(
            ["powerprofilesctl", "get"], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0:
            profile = out.stdout.strip()
        else:
            set_metric_error(vitals, "power_mode", f"powerprofilesctl failed: {out.stderr.strip() or 'no error text returned'}")
    except FileNotFoundError:
        if is_lenovo_host():
            legion_cli = os.environ.get("SV_LENOVO_LEGION_CLI") or shutil.which("legion_cli") or shutil.which("legion-cli")
            if legion_cli:
                # LenovoLegionLinux's CLI is intentionally consulted only to
                # validate its installation. Its public commands write fan
                # curves and do not offer a safe read-only profile query.
                check = subprocess.run([legion_cli, "--help"], capture_output=True, text=True, timeout=3)
                if check.returncode != 0:
                    set_metric_error(vitals, "power_mode", f"LenovoLegionLinux CLI at {legion_cli} could not run: {check.stderr.strip() or 'unknown error'}")
                else:
                    set_metric_error(vitals, "power_mode", "LenovoLegionLinux CLI is installed, but the active profile is not exposed; enable its platform-profile driver or power-profiles-daemon")
            else:
                set_metric_error(vitals, "power_mode", "Lenovo host detected but LenovoLegionLinux CLI was not found; install it or expose /sys/firmware/acpi/platform_profile")
        else:
            set_metric_error(vitals, "power_mode", "powerprofilesctl is not installed and no platform profile is exposed")
    except (OSError, subprocess.SubprocessError) as error:
        set_metric_error(vitals, "power_mode", f"cannot query Linux power profile: {error}")

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

    if label == "unknown":
        set_metric_error(vitals, "power_mode", "Linux did not report an active power profile")
    return label


def mark_unavailable_metrics(vitals: dict) -> None:
    """Make every missing dashboard metric explain itself to the user."""
    reasons = {
        "cpu_temp": "CPU temperature is not exposed by the installed hardware driver",
        "cpu_power": "CPU package power is not exposed by the installed hardware driver",
        "cpu_clock": "current CPU clock is not exposed by the operating system",
        "cpu_util": "CPU utilization is not available",
        "gpu_temp": "no readable NVIDIA GPU sensor was found (AMD/Intel GPU telemetry is not configured)",
        "gpu_power": "no readable NVIDIA GPU power sensor was found (AMD/Intel GPU telemetry is not configured)",
        "gpu_util": "no readable NVIDIA GPU utilization sensor was found (AMD/Intel GPU telemetry is not configured)",
        "gpu_mem_used": "no readable NVIDIA GPU memory sensor was found (AMD/Intel GPU telemetry is not configured)",
        "gpu_mem_total": "no readable NVIDIA GPU memory sensor was found (AMD/Intel GPU telemetry is not configured)",
        "battery_level": "this device does not expose a battery charge sensor",
        "battery_power": "this device does not expose battery charge/discharge power",
        "battery_voltage": "this device does not expose battery voltage",
        "ac_plugged": "this device does not expose AC-adapter status",
    }
    for metric, reason in reasons.items():
        if vitals.get(metric) is None:
            set_metric_error(vitals, metric, reason)


def send_reading(vitals: dict, power_mode: str):
    payload = {
        "device_secret": DEVICE_SECRET,
        "os_name": vitals.get("os_name"),
        "os_version": vitals.get("os_version"),
        "manufacturer": vitals.get("manufacturer"),
        "model": vitals.get("model"),
        "cpu_name": vitals.get("cpu_name"),
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
        "memory_used_mb": vitals.get("memory_used_mb"),
        "applications_open": vitals.get("applications_open"),
        "uptime_seconds": vitals.get("uptime_seconds"),
        "current_user": vitals.get("current_user"),
        "power_mode": power_mode,
        "metric_errors": vitals.get("metric_errors") or None,
    }
    
    resp = HTTP_SESSION.post(
        f"{SERVER_URL}/api/ingest",
        json=payload,
        timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
    )
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
            merge_vitals(vitals, get_host_details())
            merge_vitals(vitals, get_system_details())
            gpu_vitals = gpu_monitor.get_gpu_vitals()
            vitals.update(gpu_vitals)
            
            p_status = get_power_status_vitals()
            for k, v in p_status.items():
                if k == "metric_errors":
                    vitals["metric_errors"].update(v)
                elif vitals.get(k) is None:
                    vitals[k] = v

            mode = get_power_mode(vitals)
            mark_unavailable_metrics(vitals)
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
        HTTP_SESSION.close()
        gpu_monitor.shutdown()
        if IS_WINDOWS and _computer is not None:
            try:
                _computer.Close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
