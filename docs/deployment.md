# Production deployment checklist

## Native Ubuntu on WSL (no Docker)

This project can run directly in an Ubuntu WSL distribution. Cloudflare Tunnel
handles the public TLS connection, Caddy serves the frontend over loopback HTTP,
and a systemd-managed Uvicorn process runs the API on
`127.0.0.1:8000`. The supplied native configuration is for
`sysvitals.mukhildharan.dev`.

Cloudflare Tunnel creates an outbound connection from WSL to Cloudflare. The
app therefore needs no public IP, router port-forwarding, Windows firewall
rule, or WSL port-proxy configuration.

### 1. Install and enable Ubuntu systemd

Run the following from an elevated PowerShell window. The first command
installs Ubuntu if it is not already installed; launch Ubuntu once afterwards
to create its Linux user account.

```powershell
wsl --install -d Ubuntu
```

Inside Ubuntu, enable systemd:

```bash
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
```

Then return to PowerShell and restart WSL:

```powershell
wsl --shutdown
wsl -d Ubuntu
```

### 2. Deploy the application in Ubuntu

Clone the repository into Ubuntu's Linux filesystem (for example,
`~/SysVitals`), rather than running it directly from `/mnt/c`. From the
checkout, run:

```bash
chmod +x deploy/wsl-native-deploy.sh
./deploy/wsl-native-deploy.sh
systemctl status sysvitals-backend caddy --no-pager
curl -fsS http://127.0.0.1:8000/health
```

The API database is kept at `/var/lib/sysvitals/sysvitals.db`. The service is
not publicly exposed. Caddy listens only on `127.0.0.1:8080` for the tunnel.

### 3. Create and connect a Cloudflare Tunnel

In the Cloudflare Zero Trust dashboard, open **Networks > Tunnels**, create a
remotely-managed tunnel named `sysvitals-wsl`, then add a public hostname:

- **Hostname:** `sysvitals.mukhildharan.dev`
- **Service type:** `HTTP`
- **URL:** `http://127.0.0.1:8080`

Copy the connector token shown by Cloudflare. Treat it like a password: do not
put it in `.env`, source code, or git. Inside Ubuntu, install `cloudflared`
from Cloudflare's package repository and register it as a system service:

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
sudo cloudflared service install <PASTE_THE_TUNNEL_TOKEN_HERE>
sudo systemctl status cloudflared --no-pager
```

The Cloudflare dashboard creates the proxied DNS record for this hostname. Do
not create a public A/AAAA record that points at the home connection. HTTPS is
terminated by Cloudflare, while Caddy remains plain HTTP on loopback.

### 4. Verify externally

Use mobile data or another network, then run:

```bash
curl -fsS https://sysvitals.mukhildharan.dev/health
```

It must return `{"status":"ok"}`. Check connector and application logs with:

```bash
journalctl -u cloudflared -u caddy -u sysvitals-backend -n 100 --no-pager
```

To update the native deployment later, pull the new code in the Ubuntu checkout
and rerun `./deploy/wsl-native-deploy.sh`.

## Docker deployment

## Before deployment

1. Install Docker Engine and the Docker Compose plugin.
2. Point the domain's A/AAAA records to the server.
3. Allow inbound TCP 80/443 and UDP 443.
4. Copy `.env.example` to the root `.env`.
5. Set `DOMAIN` and `TW_SERVER_URL` to the public origin, then keep `.env` out of version control.
6. If Cloudflare proxying is enabled, select **Full (strict)** SSL/TLS mode.

## Start and verify

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 caddy backend
curl https://status.example.com/health
```

Expected health response:

```json
{"status":"ok"}
```

Only Caddy should publish host ports. `docker compose ps` should show no published port for `backend`.

## Routing

- `/`, HTML, CSS, JavaScript, and assets are served from `frontend/`.
- `/api/*` is passed to `backend:8000` without rewriting the path.
- `/health` is passed to the backend.

The frontend uses same-origin API paths. The monitor should set `TW_SERVER_URL` to the public origin, without a trailing `/api` segment.

## Troubleshooting

- Certificate issuance failures: verify public DNS, ports 80/443, and Caddy logs.
- Cloudflare redirect loops: verify the zone is not using Flexible SSL.
- Backend unhealthy: inspect `docker compose logs backend` and volume permissions.
- Monitor 401 response: replace `TW_DEVICE_SECRET` with the value generated for that device.
- Monitor connection failure: verify the public `/health` endpoint from the monitored computer.

Backup and update procedures are in the repository [README](../README.md).
