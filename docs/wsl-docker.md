# Run SysVitals with Docker Engine in Ubuntu on WSL

This setup does **not** need Docker Desktop. Docker Engine and Compose run
inside an Ubuntu WSL 2 distribution; Windows reaches the app through
`http://localhost:8080`.

## 1. Enable systemd in WSL (one time)

In Ubuntu, create or edit `/etc/wsl.conf` so it contains:

```ini
[boot]
systemd=true
```

Then, in **Windows PowerShell**, restart WSL:

```powershell
wsl --shutdown
```

Open Ubuntu again and check that `systemctl` works:

```bash
systemctl is-system-running
```

If your WSL version cannot use systemd, Docker can be started for the current
session with `sudo service docker start` instead.

## 2. Install Docker Engine and Compose in Ubuntu

Run these commands in the Ubuntu terminal. They use Docker's official Ubuntu
package repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Allow your Ubuntu user to use Docker without `sudo`, then close and reopen the
Ubuntu terminal (or run `newgrp docker`):

```bash
sudo usermod -aG docker $USER
```

Verify the installation:

```bash
docker run --rm hello-world
docker compose version
```

Docker's `docker` group is effectively root-level access; only add accounts
you trust to it.

## 3. Start SysVitals locally

Keep the project in the Linux filesystem for better bind-mount performance. If
it currently lives at `C:\Projects\SysVitals`, copy it to your Ubuntu home
directory first (or clone it there). From Ubuntu:

```bash
mkdir -p ~/src
cp -a /mnt/c/Projects/SysVitals ~/src/SysVitals
cd SysVitals
cp .env.wsl.example .env
docker compose up -d --build
docker compose ps
curl http://localhost:8080/health
```

Open `http://localhost:8080` in a Windows browser. WSL 2 forwards this local
port to Windows automatically. The expected health response is
`{"status":"ok"}`.

The configuration deliberately uses plain HTTP and ports 8080/8443 for local
development. It is not a public HTTPS deployment. For a public server, use
the production `.env` settings and the deployment guide instead.

## Useful commands

```bash
docker compose logs -f caddy backend
docker compose down
docker compose up -d --build
```

`docker compose down` preserves the `sysvitals_data` database volume. Do not
add `-v` unless you intentionally want to delete local data.
