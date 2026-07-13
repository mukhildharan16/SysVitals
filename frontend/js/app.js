const COOL = getComputedStyle(document.documentElement).getPropertyValue('--cool').trim();
const WARM = getComputedStyle(document.documentElement).getPropertyValue('--warm').trim();
const HOT  = getComputedStyle(document.documentElement).getPropertyValue('--hot').trim();
const CRIT = getComputedStyle(document.documentElement).getPropertyValue('--crit').trim();
const isDesktopApp = Boolean(window.__TAURI__?.core?.invoke);
let apiServerUrl = localStorage.getItem('SV_API_SERVER_URL') || '';
let accessToken = localStorage.getItem('TW_ACCESS_TOKEN') || '';

function normalizedApiServerUrl(value) {
  const url = String(value || '').trim().replace(/\/+$/, '');
  if (!url) return '';
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}

function saveApiServerUrl(value) {
  apiServerUrl = normalizedApiServerUrl(value);
  localStorage.setItem('SV_API_SERVER_URL', apiServerUrl);
  document.querySelectorAll('.api-server-input').forEach((input) => {
    input.value = apiServerUrl;
  });
}

function jsonApiUrl() {
  if (!activeDeviceId) return '';
  const origin = apiServerUrl || window.location.origin;
  return `${origin}/api/device/${encodeURIComponent(activeDeviceId)}/telemetry.json`;
}

async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 90000);
  try {
    if (isDesktopApp) {
      if (!apiServerUrl) throw new Error('Set the SysVitals API URL before signing in.');
      const response = await window.__TAURI__.core.invoke('api_request', {
        baseUrl: apiServerUrl,
        path,
        method: options.method || 'GET',
        body: options.body || null,
        authorization: accessToken ? `Bearer ${accessToken}` : null,
      });
      return {
        ok: response.status >= 200 && response.status < 300,
        status: response.status,
        headers: new Headers({ 'content-type': response.content_type || '' }),
        json: async () => response.body ? JSON.parse(response.body) : {},
      };
    }
    const headers = new Headers(options.headers || {});
    if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
    return await fetch(path, { ...options, headers, signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
}

async function readApiBody(response) {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return response.json();
  return {};
}

function networkErrorMessage(error, action) {
  if (error && error.name === 'AbortError') {
    return `API timed out during ${action}. Please try again.`;
  }
  return `Cannot reach the SysVitals API during ${action}. Please try again shortly.`;
}

// Session State
let userId = localStorage.getItem('TW_USER_ID') || '';
let username = localStorage.getItem('TW_USERNAME') || '';
let activeDeviceId = null;
let telemetryTimer = null;
let devicesTimer = null;
let refreshInProgress = false;
let devicesLoadInProgress = false;
// The monitor uploads twice a second; refresh the visible metrics every second
// so the dashboard feels live without creating unnecessary API traffic.
const TELEMETRY_REFRESH_MS = 500;

function hasAuthenticatedSession() {
  return Boolean(userId && accessToken);
}

function clearIncompleteSession() {
  if (Boolean(userId) !== Boolean(accessToken)) {
    localStorage.removeItem('TW_USER_ID');
    localStorage.removeItem('TW_USERNAME');
    localStorage.removeItem('TW_ACCESS_TOKEN');
    userId = '';
    username = '';
    accessToken = '';
  }
}

function navigateTo(path) {
  window.location.replace(path);
}

// Thresholds tuned for typical components
const CPU_THRESH = [ [70, COOL], [80, WARM], [90, HOT], [999, CRIT] ];
const GPU_THRESH = [ [60, COOL], [70, WARM], [80, HOT], [999, CRIT] ];
const P_THRESH = [ [20, COOL], [40, WARM], [60, HOT], [999, CRIT] ];

const FAN_CURVES = {
  silent: {
    cpu: [[0, 0], [50, 0], [60, 2200], [70, 2600], [80, 3000], [90, 3400], [100, 3600], [120, 3600]],
    gpu: [[0, 0], [50, 0], [60, 2200], [70, 2600], [80, 3200], [90, 3600], [100, 3800], [120, 3800]]
  },
  balanced: {
    cpu: [[0, 0], [40, 0], [50, 2200], [60, 2400], [70, 2800], [80, 3200], [90, 3800], [100, 4400], [120, 4400]],
    gpu: [[0, 0], [40, 0], [50, 2200], [60, 2400], [70, 2900], [80, 3500], [90, 4100], [100, 4600], [120, 4600]]
  },
  turbo: {
    cpu: [[0, 2200], [20, 2200], [50, 2600], [60, 3000], [70, 3400], [80, 4000], [90, 4600], [100, 5000], [120, 5000]],
    gpu: [[0, 2200], [20, 2200], [50, 2600], [60, 3000], [70, 3500], [80, 4200], [90, 4800], [100, 5200], [120, 5200]]
  }
};

function getFanSpeed(mode, temp, isGpu) {
  let profile = 'balanced';
  const m = String(mode || '').toLowerCase();
  if (m.includes('silent') || m.includes('quiet') || m.includes('power-saver')) {
    profile = 'silent';
  } else if (m.includes('turbo') || m.includes('performance')) {
    profile = 'turbo';
  }
  
  const curve = isGpu ? FAN_CURVES[profile].gpu : FAN_CURVES[profile].cpu;
  
  if (temp <= curve[0][0]) return curve[0][1];
  if (temp >= curve[curve.length - 1][0]) return curve[curve.length - 1][1];
  
  for (let i = 0; i < curve.length - 1; i++) {
    const [t0, r0] = curve[i];
    const [t1, r1] = curve[i+1];
    if (temp >= t0 && temp <= t1) {
      const pct = (temp - t0) / (t1 - t0);
      return Math.round(r0 + pct * (r1 - r0));
    }
  }
  return 0;
}

function colorForCPUTemp(t){
  for (const [max, c] of CPU_THRESH) if (t <= max) return c;
  return CRIT;
}

function colorForGPUTemp(t){
  for (const [max, c] of GPU_THRESH) if (t <= max) return c;
  return CRIT;
}

function colorForPower(p){
  for (const [max, c] of P_THRESH) if (p <= max) return c;
  return CRIT;
}

function setGauge(arcId, tempId, temp, isGpu = false){
  const circumference = 2 * Math.PI * 100; // ~628
  const maxTemp = isGpu ? 90 : 105;
  const pct = Math.max(0, Math.min(1, temp / maxTemp));
  const offset = circumference * (1 - pct);
  const arc = document.getElementById(arcId);
  if (!arc) return;
  arc.style.strokeDasharray = circumference;
  arc.style.strokeDashoffset = offset;
  
  const c = isGpu ? colorForGPUTemp(temp) : colorForCPUTemp(temp);
  arc.style.stroke = c;
  
  const tempEl = document.getElementById(tempId);
  if (tempEl) {
    tempEl.style.color = c;
    tempEl.textContent = temp > 0 ? temp.toFixed(1) : '--';
  }
  
  const parent = arc.closest('.gauge');
  if (parent) {
    parent.style.filter = `drop-shadow(0 0 16px ${c}33)`;
  }
}

// Power gauge setup
function setPowerGauge(arcId, valId, power){
  const circumference = 2 * Math.PI * 100; // ~628
  const pct = Math.max(0, Math.min(1, power / 170));
  const offset = circumference * (1 - pct);
  const arc = document.getElementById(arcId);
  if (!arc) return;
  arc.style.strokeDasharray = circumference;
  arc.style.strokeDashoffset = offset;
  
  const c = colorForPower(power);
  arc.style.stroke = c;
  
  const valEl = document.getElementById(valId);
  if (valEl) {
    valEl.style.color = c;
    valEl.textContent = power > 0 ? power.toFixed(1) : '--';
  }
  
  const parent = arc.closest('.gauge');
  if (parent) {
    parent.style.filter = `drop-shadow(0 0 16px ${c}33)`;
  }
}

function setModeChip(mode){
  const chip = document.getElementById('modeChip');
  if (!chip) return;
  if (!mode) {
    chip.textContent = 'unknown';
    chip.style.borderColor = '#7C8798';
    chip.style.color = '#7C8798';
    chip.style.boxShadow = 'none';
    return;
  }
  chip.textContent = mode;
  const lowerMode = mode.toLowerCase();
  const c = lowerMode.includes('turbo') || lowerMode.includes('performance') ? HOT
           : lowerMode.includes('quiet') || lowerMode.includes('power-saver') ? COOL
           : WARM;
  chip.style.borderColor = c;
  chip.style.color = c;
  chip.style.boxShadow = `0 0 16px ${c}33`;
}

// Navigation & Auth Flow
function toggleAuth(showLogin) {
  const loginCard = document.getElementById('loginCard');
  const registerCard = document.getElementById('registerCard');
  if (loginCard) loginCard.style.display = showLogin ? 'flex' : 'none';
  if (registerCard) registerCard.style.display = showLogin ? 'none' : 'flex';
  const loginErr = document.getElementById('loginError');
  const regErr = document.getElementById('regError');
  if (loginErr) loginErr.textContent = '';
  if (regErr) regErr.textContent = '';
}

async function handleRegister() {
  if (isDesktopApp) saveApiServerUrl(document.getElementById('registerApiUrl')?.value);
  const user = document.getElementById('regUser').value.trim();
  const pass = document.getElementById('regPass').value;
  const confirm = document.getElementById('regConfirm').value;
  const errEl = document.getElementById('regError');
  if (errEl) errEl.textContent = '';

  if (!user || !pass) {
    if (errEl) errEl.textContent = 'Username and password required';
    return;
  }
  if (pass !== confirm) {
    if (errEl) errEl.textContent = 'Passwords do not match';
    return;
  }

  try {
    const res = await apiFetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password: pass })
    });
    const d = await readApiBody(res);
    if (!res.ok) {
      if (errEl) errEl.textContent = d.detail || 'Registration failed';
      return;
    }
    // Switch to login
    toggleAuth(true);
    const loginUserEl = document.getElementById('loginUser');
    if (loginUserEl) loginUserEl.value = user;
    const loginErrorEl = document.getElementById('loginError');
    if (loginErrorEl) {
      loginErrorEl.style.color = '#5FD48A';
      loginErrorEl.textContent = 'Account created! Please login.';
    }
  } catch (err) {
    if (errEl) errEl.textContent = networkErrorMessage(err, 'registration');
  }
}

async function handleLogin() {
  if (isDesktopApp) saveApiServerUrl(document.getElementById('loginApiUrl')?.value);
  const user = document.getElementById('loginUser').value.trim();
  const pass = document.getElementById('loginPass').value;
  const errEl = document.getElementById('loginError');
  if (errEl) {
    errEl.style.color = varName('--hot');
    errEl.textContent = '';
  }

  if (!user || !pass) {
    if (errEl) errEl.textContent = 'Username and password required';
    return;
  }

  try {
    const res = await apiFetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password: pass })
    });
    const d = await readApiBody(res);
    if (!res.ok) {
      if (errEl) errEl.textContent = d.detail || 'Invalid credentials';
      return;
    }
    
    userId = d.user_id;
    username = user;
    accessToken = d.access_token;
    localStorage.setItem('TW_USER_ID', userId);
    localStorage.setItem('TW_USERNAME', username);
    localStorage.setItem('TW_ACCESS_TOKEN', accessToken);
    navigateTo('dashboard.html');
  } catch (err) {
    if (errEl) errEl.textContent = networkErrorMessage(err, 'login');
  }
}

function handleLogout() {
  localStorage.removeItem('TW_USER_ID');
  localStorage.removeItem('TW_USERNAME');
  localStorage.removeItem('TW_ACCESS_TOKEN');
  userId = '';
  username = '';
  accessToken = '';
  activeDeviceId = null;
  navigateTo('login.html');
}

async function copyTelemetryJsonUrl() {
  const url = jsonApiUrl();
  if (!url || !accessToken) return;
  const command = `curl -H \"Authorization: Bearer ${accessToken}\" \"${url}\"`;
  try {
    await navigator.clipboard.writeText(command);
    const button = document.querySelector('[onclick="copyTelemetryJsonUrl()"]');
    if (button) {
      button.textContent = 'Protected API Command Copied';
      setTimeout(() => { button.textContent = 'Copy Protected API Command'; }, 2000);
    }
  } catch (error) {
    window.prompt('Copy this protected API command:', command);
  }
}

function initView() {
  if (isDesktopApp) {
    document.body.classList.add('desktop-app');
    document.querySelectorAll('.api-server-input').forEach((input) => {
      input.value = apiServerUrl;
    });
  }
  const isLoginPage = !!document.getElementById('authContainer');
  const isDashboardPage = !!document.getElementById('dashboardContainer');
  
  if (isLoginPage) {
    clearIncompleteSession();
    if (hasAuthenticatedSession()) {
      navigateTo('dashboard.html');
      return;
    }
    toggleAuth(true);
  } else if (isDashboardPage) {
    if (!hasAuthenticatedSession()) {
      clearIncompleteSession();
      navigateTo('login.html');
      return;
    }
    
    if (telemetryTimer) clearInterval(telemetryTimer);
    if (devicesTimer) clearInterval(devicesTimer);
    
    if (activeDeviceId) {
      document.getElementById('dashboardContainer').style.display = 'none';
      document.getElementById('telemetryContainer').style.display = 'block';
      const gpuAwakeEl = document.getElementById('gpuLastAwake');
      if (gpuAwakeEl) gpuAwakeEl.textContent = '—';
      refreshLatest();
      // Only metric elements change in refreshLatest; polling every second
      // prevents the gauge transitions from looking like a full-page refresh.
      telemetryTimer = setInterval(refreshLatest, TELEMETRY_REFRESH_MS);
    } else {
      document.getElementById('dashboardContainer').style.display = 'block';
      document.getElementById('telemetryContainer').style.display = 'none';
      document.getElementById('dashboardUsername').textContent = username;
      document.getElementById('hostLabel').textContent = 'account active';
      document.getElementById('liveDot').classList.add('live');
      
      document.getElementById('eyebrowLabel').textContent = '// Thermal Watch';
      document.getElementById('brandTitle').textContent = 'My Devices';
      
      loadDevices();
      devicesTimer = setInterval(loadDevices, 5000);
    }
  }
}

// Device Dashboard Functions
async function loadDevices() {
  if (devicesLoadInProgress) return;
  devicesLoadInProgress = true;
  try {
    const res = await apiFetch(`/api/user/${userId}/devices`);
    if (!res.ok) throw new Error('Failed to load devices');
    const devices = await res.json();
    
    const listEl = document.getElementById('devicesList');
    if (!listEl) return;
    listEl.innerHTML = '';
    
    if (devices.length === 0) {
      listEl.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 48px; border: 1px dashed var(--border); border-radius: 14px; color: var(--muted); font-size: 13px;">
          No devices registered yet. Click "Add Device +" to register a device.
        </div>
      `;
      return;
    }
    
    devices.forEach(dev => {
      let active = false;
      let lastSeenStr = 'Never';
      if (dev.last_seen) {
        const lastSeenDate = new Date(dev.last_seen);
        const diffSeconds = (new Date() - lastSeenDate) / 1000;
        active = diffSeconds < 15;
        lastSeenStr = lastSeenDate.toLocaleTimeString();
      }
      
      const card = document.createElement('div');
      card.className = 'device-card';
      card.innerHTML = `
        <div class="device-header">
          <div class="device-name">${dev.name}</div>
          <div class="device-status" style="color: ${active ? '#5FD48A' : 'var(--muted)'}; border-color: ${active ? '#5FD48A44' : 'var(--border)'}">
            <span class="dot" style="background: ${active ? '#5FD48A' : 'var(--muted)'}; width: 6px; height: 6px; box-shadow: ${active ? '0 0 6px #5FD48A' : 'none'}"></span>
            ${active ? 'Online' : 'Offline'}
          </div>
        </div>
        <div class="device-details">
          <span>Hostname: <b>${dev.hostname || '—'}</b></span>
          <span>Last Seen: <b>${lastSeenStr}</b></span>
        </div>
        <div class="device-actions">
          <button class="btn-submit" style="width: 100%; font-size: 12px; padding: 10px;" onclick="openDeviceDashboard('${dev.id}', '${dev.name}')">Open Dashboard</button>
        </div>
      `;
      listEl.appendChild(card);
    });
  } catch (err) {
    console.error(err);
  } finally {
    devicesLoadInProgress = false;
  }
}

// Modal Handlers
function openAddDeviceModal() {
  const modal = document.getElementById('addDeviceModal');
  if (modal) modal.style.display = 'flex';
  const nameInput = document.getElementById('deviceNameInput');
  const hostInput = document.getElementById('deviceHostInput');
  if (nameInput) nameInput.value = '';
  if (hostInput) hostInput.value = '';
  const modalForm = document.getElementById('modalForm');
  const modalSuccess = document.getElementById('modalSuccess');
  if (modalForm) modalForm.style.display = 'block';
  if (modalSuccess) modalSuccess.style.display = 'none';
  const modalErr = document.getElementById('modalError');
  if (modalErr) modalErr.textContent = '';
}

function closeAddDeviceModal() {
  const modal = document.getElementById('addDeviceModal');
  if (modal) modal.style.display = 'none';
}

async function submitAddDevice() {
  const dName = document.getElementById('deviceNameInput').value.trim();
  const dHost = document.getElementById('deviceHostInput').value.trim();
  const errEl = document.getElementById('modalError');
  if (errEl) errEl.textContent = '';
  
  if (!dName) {
    if (errEl) errEl.textContent = 'Device name is required';
    return;
  }
  
  try {
    const res = await apiFetch('/api/device/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, device_name: dName, hostname: dHost || null })
    });
    const d = await res.json();
    if (!res.ok) {
      if (errEl) errEl.textContent = d.detail || 'Failed to register device';
      return;
    }
    
    const secretVal = document.getElementById('generatedSecretVal');
    if (secretVal) secretVal.textContent = d.device_secret;
    const modalForm = document.getElementById('modalForm');
    const modalSuccess = document.getElementById('modalSuccess');
    if (modalForm) modalForm.style.display = 'none';
    if (modalSuccess) modalSuccess.style.display = 'block';
  } catch (err) {
    if (errEl) errEl.textContent = 'Network error registering device';
  }
}

function copyDeviceSecret() {
  const secretText = document.getElementById('generatedSecretVal').textContent;
  navigator.clipboard.writeText(secretText).then(() => {
    const copyBtn = document.querySelector('.btn-copy');
    if (copyBtn) {
      copyBtn.textContent = 'Copied!';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
    }
  });
}

function finishAddDevice() {
  closeAddDeviceModal();
  loadDevices();
}

// Telemetry Handlers
function openDeviceDashboard(devId, devName) {
  activeDeviceId = devId;
  const eyebrow = document.getElementById('eyebrowLabel');
  const title = document.getElementById('brandTitle');
  if (eyebrow) eyebrow.textContent = `// Device: ${devName}`;
  if (title) title.textContent = 'Live Telemetry';
  initView();
}

function goBackToDevices() {
  activeDeviceId = null;
  initView();
}

async function refreshLatest(){
  if (!activeDeviceId || refreshInProgress) return;
  refreshInProgress = true;
  try{
    const res = await apiFetch(`/api/device/${activeDeviceId}/latest`);
    if (!res.ok) throw new Error('no data');
    const d = await res.json();
    const liveDot = document.getElementById('liveDot');
    const hostLabel = document.getElementById('hostLabel');
    if (liveDot) liveDot.classList.add('live');
    if (hostLabel) hostLabel.textContent = 'online';

    const cpuPanelTitle = document.getElementById('cpuPanelTitle');
    if (cpuPanelTitle) cpuPanelTitle.textContent = d.cpu_name || 'CPU';
    const gpuPanelTitle = document.getElementById('gpuPanelTitle');
    if (gpuPanelTitle) gpuPanelTitle.textContent = d.gpu_name || 'GPU';
    
    // CPU
    const cpuTemp = typeof d.cpu_temp === 'number' ? d.cpu_temp : 0;
    setGauge('cpuGaugeArc', 'cpuGaugeTemp', cpuTemp, false);
    
    const cpuUtilEl = document.getElementById('cpuUtilVal');
    const cpuPowerEl = document.getElementById('cpuPowerVal');
    const cpuClockEl = document.getElementById('cpuClockVal');
    if (cpuUtilEl) cpuUtilEl.textContent = typeof d.cpu_util === 'number' ? `${d.cpu_util.toFixed(0)}%` : '—';
    if (cpuPowerEl) cpuPowerEl.textContent = typeof d.cpu_power === 'number' ? `${d.cpu_power.toFixed(1)} W` : '—';
    if (cpuClockEl) cpuClockEl.textContent = typeof d.cpu_clock === 'number' ? `${d.cpu_clock.toFixed(0)} MHz` : '—';
    
    const cpuFanSpeed = getFanSpeed(d.power_mode, cpuTemp, false);
    const cpuFanEl = document.getElementById('cpuFanVal');
    if (cpuFanEl) cpuFanEl.textContent = cpuFanSpeed > 0 ? `${cpuFanSpeed} RPM` : '0 RPM (OFF)';

    // GPU
    const gpuTemp = typeof d.gpu_temp === 'number' ? d.gpu_temp : 0;
    setGauge('gpuGaugeArc', 'gpuGaugeTemp', gpuTemp, true);
    
    const gpuUtilEl = document.getElementById('gpuUtilVal');
    const gpuPowerEl = document.getElementById('gpuPowerVal');
    if (gpuUtilEl) gpuUtilEl.textContent = typeof d.gpu_util === 'number' ? `${d.gpu_util.toFixed(0)}%` : '—';
    if (gpuPowerEl) gpuPowerEl.textContent = typeof d.gpu_power === 'number' ? `${d.gpu_power.toFixed(1)} W` : '—';
    
    const gpuFanEl = document.getElementById('gpuFanVal');
    if (gpuFanEl) {
      if (!d.gpu_name) {
        gpuFanEl.textContent = '—';
      } else if (d.gpu_active === false) {
        gpuFanEl.textContent = '0 RPM (Sleeping)';
      } else {
        const gpuFanSpeed = getFanSpeed(d.power_mode, gpuTemp, true);
        gpuFanEl.textContent = gpuFanSpeed > 0 ? `${gpuFanSpeed} RPM` : '0 RPM (OFF)';
      }
    }
    
    const gpuLastAwakeEl = document.getElementById('gpuLastAwake');
    if (gpuLastAwakeEl && d.gpu_name && d.gpu_active !== false) {
      const gpuDate = new Date(d.ts * 1000);
      let gHours = gpuDate.getHours();
      const gAmpm = gHours >= 12 ? 'PM' : 'AM';
      gHours = gHours % 12;
      gHours = gHours ? gHours : 12;
      const gMins = String(gpuDate.getMinutes()).padStart(2, '0');
      const gSecs = String(gpuDate.getSeconds()).padStart(2, '0');
      gpuLastAwakeEl.textContent = `${gHours}:${gMins}:${gSecs} ${gAmpm}`;
    }
    
    const gpuVramEl = document.getElementById('gpuVramVal');
    if (gpuVramEl) {
      if (typeof d.gpu_mem_used === 'number' && typeof d.gpu_mem_total === 'number' && d.gpu_mem_total > 0) {
        const usedGB = d.gpu_mem_used / 1024;
        const totalGB = d.gpu_mem_total / 1024;
        const pct = (d.gpu_mem_used / d.gpu_mem_total) * 100;
        gpuVramEl.textContent = `${usedGB.toFixed(2)} GB / ${totalGB.toFixed(1)} GB (${pct.toFixed(0)}%)`;
      } else {
        gpuVramEl.textContent = '—';
      }
    }

    const gpuChip = document.getElementById('gpuChip');
    if (gpuChip) {
      if (d.gpu_name) {
        if (d.gpu_active === false) {
          gpuChip.textContent = 'GPU Sleeping';
          gpuChip.style.borderColor = '#7C8798';
          gpuChip.style.color = '#7C8798';
          gpuChip.style.boxShadow = 'none';
        } else {
          gpuChip.textContent = 'GPU Active';
          gpuChip.style.borderColor = COOL;
          gpuChip.style.color = COOL;
          gpuChip.style.boxShadow = `0 0 16px ${COOL}33`;
        }
      } else {
        gpuChip.textContent = 'No GPU Detected';
        gpuChip.style.borderColor = '#7C8798';
        gpuChip.style.color = '#7C8798';
        gpuChip.style.boxShadow = 'none';
      }
    }

    // Power Analytics
    const cpuP = typeof d.cpu_power === 'number' ? d.cpu_power : 0;
    const gpuP = typeof d.gpu_power === 'number' ? d.gpu_power : 0;
    const batP = typeof d.battery_power === 'number' ? d.battery_power : 0;
    
    let totalPower = 0;
    let powerSourceStr = 'Unknown';
    let batteryStatusStr = '—';
    
    if (d.ac_plugged === true) {
      powerSourceStr = 'AC Power';
      totalPower = cpuP + gpuP;
      if (typeof d.battery_level === 'number') {
        if (d.battery_level >= 99) {
          batteryStatusStr = `Fully Charged (${d.battery_level.toFixed(0)}%)`;
        } else if (batP > 0) {
          batteryStatusStr = `Charging (${d.battery_level.toFixed(0)}%)`;
        } else {
          batteryStatusStr = `Plugged In (${d.battery_level.toFixed(0)}%)`;
        }
      } else {
        batteryStatusStr = 'Plugged In';
      }
    } else if (d.ac_plugged === false) {
      powerSourceStr = 'Battery';
      totalPower = batP > 0 ? batP : (cpuP + gpuP);
      if (typeof d.battery_level === 'number') {
        batteryStatusStr = `Discharging (${d.battery_level.toFixed(0)}%)`;
      } else {
        batteryStatusStr = 'Discharging';
      }
    }
    
    setPowerGauge('powerGaugeArc', 'powerGaugeVal', totalPower);
    
    const cpuPowerVal2El = document.getElementById('cpuPowerVal2');
    const gpuPowerVal2El = document.getElementById('gpuPowerVal2');
    if (cpuPowerVal2El) cpuPowerVal2El.textContent = typeof d.cpu_power === 'number' ? `${d.cpu_power.toFixed(1)} W` : '—';
    if (gpuPowerVal2El) gpuPowerVal2El.textContent = typeof d.gpu_power === 'number' ? `${d.gpu_power.toFixed(1)} W` : '—';
    
    const batteryPowerValEl = document.getElementById('batteryPowerVal');
    if (batteryPowerValEl) {
      if (typeof d.battery_power === 'number') {
        const prefix = d.ac_plugged === true ? '+' : '-';
        batteryPowerValEl.textContent = `${prefix}${d.battery_power.toFixed(1)} W`;
      } else {
        batteryPowerValEl.textContent = '—';
      }
    }
    
    const batteryStatusValEl = document.getElementById('batteryStatusVal');
    if (batteryStatusValEl) batteryStatusValEl.textContent = batteryStatusStr;
    
    const pChip = document.getElementById('powerSourceChip');
    if (pChip) {
      pChip.textContent = powerSourceStr;
      const c = d.ac_plugged === true ? COOL : WARM;
      pChip.style.borderColor = c;
      pChip.style.color = c;
      pChip.style.boxShadow = `0 0 16px ${c}33`;
    }

    // Info/mode
    setModeChip(d.power_mode);
    const lastUpdatedDate = new Date(d.ts * 1000);
    let hours = lastUpdatedDate.getHours();
    const ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12;
    hours = hours ? hours : 12;
    const mins = String(lastUpdatedDate.getMinutes()).padStart(2, '0');
    const secs = String(lastUpdatedDate.getSeconds()).padStart(2, '0');
    const lastUpdatedEl = document.getElementById('lastUpdated');
    if (lastUpdatedEl) lastUpdatedEl.textContent = `${hours}:${mins}:${secs} ${ampm}`;
  } catch(e) {
    if (liveDot) liveDot.classList.remove('live');
    if (hostLabel) hostLabel.textContent = 'waiting for data…';
  } finally {
    refreshInProgress = false;
  }
}

// Utility to get error CSS color variable
function varName(cssVar) {
  return getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim();
}

// Initial View Setup
initView();
