# Thermal Watch

A tiny system for watching a laptop's CPU temperature and power profile
(quiet / balanced / performance / turbo) live from a website, plus history
and computed stats.

Two pieces:
- **client/** — a Python script that runs *on your laptop*, reads the CPU
  temp + power profile every ~30s, and POSTs it to the backend.
- **backend/** — a FastAPI app that stores readings (SQLite) and serves the
  dashboard at `/`.

## 1. Run the backend

```bash
cd backend
pip install -r requirements.txt
export INGEST_API_KEY="pick-a-long-random-string"
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` — you'll see the dashboard (empty until the
client sends data).

`INGEST_API_KEY` is the only thing standing between "just your laptop can
post readings" and "anyone on the internet can post fake readings." Reads
(the dashboard itself) are intentionally public — no login needed to view
it — but writes require this key. Pick something long and random, e.g.:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 2. Run the client (on your Fedora laptop)

```bash
cd client
pip install -r requirements.txt
export TW_SERVER_URL="https://your-deployed-backend.example.com"
export TW_API_KEY="the same string as INGEST_API_KEY above"
python3 monitor.py
```

It reads CPU temp via `psutil`/lm-sensors (falls back to
`/sys/class/thermal`), and the power profile via `powerprofilesctl get`
(GNOME's power-profiles-daemon — this is what Fedora's Settings > Power
panel uses). If it's in `performance` mode and CPU boost is on
(`/sys/devices/system/cpu/cpufreq/boost`), it reports `turbo` instead.

### Run it continuously (systemd user service)

```bash
mkdir -p ~/thermalwatch
cp client/monitor.py ~/thermalwatch/
cp client/thermalwatch.service ~/.config/systemd/user/
# edit ~/.config/systemd/user/thermalwatch.service with your real
# TW_SERVER_URL and TW_API_KEY first
systemctl --user daemon-reload
systemctl --user enable --now thermalwatch.service
systemctl --user status thermalwatch.service   # check it's running
journalctl --user -u thermalwatch.service -f   # watch it send readings
```

## 3. Put the backend on the public internet

Since you want it reachable from anywhere, a few options, roughly cheapest
to most hands-off:

- **A small VPS** (Hetzner ~€4/mo, DigitalOcean/Linode ~$4-6/mo): run
  `uvicorn` behind `systemd` + `nginx` (or just expose the port). SQLite
  works fine here since the disk is persistent. Most control, a bit more
  setup.
- **Fly.io**: free-ish tier, supports persistent **volumes** — important,
  because without one the SQLite file gets wiped on every redeploy.
  Attach a small volume and point `DB_PATH` at it.
- **Render**: easy to deploy, but its free tier disk is *ephemeral* — your
  history will vanish on redeploys/restarts unless you pay for a persistent
  disk add-on. Fine for testing, not for long-term history.

Whichever you pick, just set `INGEST_API_KEY` (and optionally `DB_PATH`) as
environment variables on that platform, same as running it locally.

## What the dashboard computes

- **Live gauge**: current temp, color-coded (cool → warm → hot → critical),
  current power mode.
- **History chart**: 1h / 6h / 24h / 7d views.
- **Trend**: °C/hour drift over the selected window (comparing the first
  quarter of readings to the last quarter).
- **Time-in-mode**: what % of the selected window was spent in each power
  mode, and the average temp while in that mode — useful for seeing e.g.
  "turbo mode runs ~15°C hotter and I'm in it 40% of the time."

## Notes / things you may want to change

- Temp thresholds for the gauge colors (55 / 70 / 85 °C) are set in
  `backend/static/index.html` near the top of the `<script>` block —
  adjust to whatever's normal for your CPU.
- The client currently reports one machine at a time; the `hostname` field
  is already there in the schema if you ever want to extend this to
  multiple machines with a dropdown/filter.
- No auth on the dashboard itself (by design, since you said public
  internet + wanted it simple). If you'd rather it not be fully public,
  say so and I can add a simple password gate.
