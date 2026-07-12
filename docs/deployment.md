# Production Deployment Guide

This document describes how to deploy the SysVitals dashboard frontend and backend server components to production.

---

## 1. Frontend (Cloudflare Pages)

The frontend is a completely static, serverless website. It can be hosted on any static hosting provider.

### Deployment Instructions:
1. Go to your **[Cloudflare Dashboard](https://dash.cloudflare.com/)** and navigate to **Workers & Pages**.
2. Click **Create Application** &rarr; **Pages** &rarr; **Upload assets** or connect your GitHub repository.
3. If connecting a repository, configure the build settings as:
   * **Framework preset**: `None`
   * **Build command**: *Leave empty*
   * **Build output directory**: `frontend/`
4. Click **Deploy**. Your dashboard will be live at `https://<your-project>.pages.dev`.

---

## 2. Backend (Render / Railway / Fly.io)

The backend is a standard FastAPI web application. It connects to your remote Supabase database and serves the client API routes.

### Deployment Instructions (Render):
1. Create a new **Web Service** on [Render](https://render.com).
2. Connect your GitHub repository.
3. Configure the settings:
   * **Runtime**: `Python`
   * **Build Command**: `pip install -r backend/requirements.txt`
   * **Start Command**: `python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
4. In the **Environment** tab, add the following environment variables:
   * `SUPABASE_URL`: Your Supabase project URL (e.g. `https://mffdohfthbeiuwoodqwo.supabase.co`)
   * `SUPABASE_KEY`: Your Supabase `service_role` secret key.

---

## 3. Connecting Frontend and Backend

### Central Config
You can pre-configure your dashboard frontend to default to your deployed backend URL:
1. Open **[frontend/js/config.js](file:///c:/Projects/SysVitals/frontend/js/config.js)**.
2. Edit `API_BASE_URL` to point to your live backend domain:
   ```javascript
   const API_BASE_URL = 'https://sysvitals-backend.onrender.com';
   ```

### Runtime Target Switcher
Any user can switch their active API endpoint dynamically on the dashboard website by:
1. Typing their backend URL into the **API Server URL** box in the top-right corner.
2. Appending `?backend=<url>` to the browser address bar (e.g. `https://sysvitals.pages.dev/?backend=https://sysvitals-backend.onrender.com`).

---

## 4. HTTPS and Mixed Content Block
When hosting your frontend on a secure site (`https://`):
* Modern browsers **block** requests to insecure `http://` API endpoints.
* Therefore, your hosted backend **must have SSL enabled (https://)**. Cloudflare Pages and Render automatically provision free SSL certificates for you out of the box, ensuring seamless HTTPS communication.

---

## 5. Automatic Telemetry Cleanup (10-Minute Retention)

To prevent your database storage from exceeding limits, telemetry records can be automatically cleaned up after 10 minutes.

### Setup Instructions (Supabase pg_cron):
1. Go to your **Supabase Dashboard** &rarr; **SQL Editor**.
2. Run the migration script located in **[backend/supabase_cleanup_migration.sql](file:///c:/Projects/SysVitals/backend/supabase_cleanup_migration.sql)**.
3. This creates the cleanup function `delete_old_telemetry()`, sets up an index on `ts`, and registers the recurring job `telemetry-retention-cleanup` via `pg_cron` to run every minute.

### Checking Cron Job Status:
You can check if the cron job is successfully registered and running by executing these queries in the SQL Editor:

```sql
-- View all registered cron jobs
select * from cron.job;

-- Check execution logs and success status
select * from cron.job_run_details
order by start_time desc
limit 20;
```
