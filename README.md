# SysVitals

SysVitals is a clean, modern, real-time system monitoring application that tracks a laptop's CPU and GPU metrics (temperature, utilization, clock speed, and power draw) along with G-Helper power profiles and estimated fan speeds.

---

## 1. Project Directory Structure

The repository is organized into three independent components:

```
SysVitals/
├── frontend/             # Static SPA (HTML/CSS/JS) deployable to Cloudflare Pages
│   ├── index.html        # Entry router (auth redirector)
│   ├── login.html        # Login / Register screens
│   ├── dashboard.html    # Devices lists & real-time telemetry gauges
│   ├── css/
│   │   └── style.css     # Unified UI styling
│   └── js/
│       ├── config.js     # Centralized API domain configuration
│       └── app.js        # Polling & dashboard logic
│
├── backend/              # FastAPI application communicating with Supabase DB
│   ├── main.py           # API endpoints (Auth, Device, Telemetry ingest)
│   ├── database.py       # Supabase REST client initialization
│   ├── models.py         # Request/Response schemas (Pydantic)
│   ├── requirements.txt  # Server Python dependencies
│   └── supabase_schema.sql
│
├── monitor/              # Local telemetry uploader client
│   ├── monitor.py        # Telemetry collection script
│   ├── requirements.txt  # Client Python dependencies
│   └── .env.example      # Sample client environment setup
│
├── docs/                 # Detailed guides
│   └── deployment.md     # Production deployment instructions
├── .gitignore
├── LICENSE
└── README.md
```

---

## 2. Local Development

### 1. Database Setup
Ensure you have a Supabase project created. Run the contents of `backend/supabase_schema.sql` inside your Supabase project's **SQL Editor** to create the tables.

### 2. Run the Backend Server
Create a `.env` file in the root `SysVitals/` directory (or inside `backend/`):
```env
SUPABASE_URL="https://your-project-id.supabase.co"
SUPABASE_KEY="your-supabase-service-role-secret-key"
```

Then, install dependencies and start the server:
```bash
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
The server will start listening at `http://127.0.0.1:8000`.

### 3. Run the Frontend Dashboard
Since the frontend is purely static, you can run it simply by opening `frontend/index.html` in any browser! Or run a local static server:
```bash
npx serve frontend
# or
python -m http.server -d frontend 3000
```
It will automatically connect to your local backend API running on port `8000` (defined in `frontend/js/config.js`).

### 4. Run the Telemetry Monitor
Create a `.env` in the root `SysVitals/` directory (or inside `monitor/`):
```env
TW_SERVER_URL="http://127.0.0.1:8000"
TW_DEVICE_SECRET="your-device-secret-from-dashboard"
TW_HOSTNAME="My-Desktop"
TW_INTERVAL_SECONDS=0.5
```

Install client dependencies and run:
```bash
pip install -r monitor/requirements.txt
python monitor/monitor.py
```
It will begin reporting system telemetry to the backend twice per second.

---

## 3. Production Deployment

Refer to **[docs/deployment.md](file:///c:/Projects/SysVitals/docs/deployment.md)** for full, step-by-step guidance on deploying:
* **Frontend**: Deploying the `/frontend` directory to **Cloudflare Pages**.
* **Backend**: Hosting the `/backend` FastAPI service on **Render**, **Railway**, or **Fly.io**.
* **Database**: Creating tables and setting up service credentials.
