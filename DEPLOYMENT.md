# Deployment Guide: Torrent Sentinel

This document outlines the procedures for deploying **Torrent Sentinel** on your production environment (Unraid / Docker) or a local machine.

---

## 1. Unraid Docker Template Deployment (Recommended for Unraid)

A native Unraid Docker XML template is provided in the repository: [`torrent-sentinel.xml`](./torrent-sentinel.xml).

### Method A: Direct Flash Drive Installation
1. Copy `torrent-sentinel.xml` to your Unraid USB flash drive at:
   ```text
   /boot/config/plugins/dockerMan/templates-user/torrent-sentinel.xml
   ```
2. In the Unraid WebUI, go to the **Docker** tab and click **Add Container**.
3. In the **Template** dropdown, select `torrent-sentinel`.
4. Adjust your configuration (Transmission host IP, Ollama base URL, paths).
5. Click **Apply**.

### Method B: Template Repository / Community Apps
Add the repository URL to your Unraid Docker template repositories:
```text
https://github.com/nate-eisner/torrent-sentinel
```
Or point directly to the raw XML URL:
```text
https://raw.githubusercontent.com/nate-eisner/torrent-sentinel/main/torrent-sentinel.xml
```

---

## 2. Docker Compose Deployment

If you prefer using Docker Compose (or Unraid's Docker Compose Manager / Dockge):

### Step 1: Prepare Directory Structure
```text
/mnt/user/appdata/torrent-sentinel/
├── vpn_configs/      <-- Place your WireGuard .conf files here
└── data/             <-- Holds sentinel.db SQLite database
```

### Step 2: Create `docker-compose.yml`
```yaml
version: '3.8'

services:
  torrent-sentinel:
    image: ghcr.io/nate-eisner/torrent-sentinel:latest
    container_name: torrent-sentinel
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
    sysctls:
      - net.ipv4.conf.all.src_valid_mark=1
    ports:
      - "8000:8000"
    environment:
      # Transmission Configuration
      - SENTINEL_TRANSMISSION_HOST=192.168.0.X   # Your Unraid IP
      - SENTINEL_TRANSMISSION_PORT=9091
      - SENTINEL_TRANSMISSION_RPC_PATH=/transmission/rpc
      - SENTINEL_TRANSMISSION_AUTH=            # user:password if needed
      
      # Ollama Configuration (Local AI diagnostics)
      - SENTINEL_OLLAMA_ENABLED=true
      - SENTINEL_OLLAMA_BASE_URL=http://192.168.0.10:11434
      - SENTINEL_OLLAMA_MODEL=gemma4:26b
      
      # VPN Configuration (Unraid WireGuard)
      - SENTINEL_VPN_TYPE=unraid_wireguard
      - SENTINEL_VPN_INTERFACE=wg1
      - SENTINEL_VPN_CONFIGS_DIR=/app/vpn_configs/
      - SENTINEL_VPN_ACTIVE_CONFIG=/app/vpn_configs/active.conf
      
      # Notification Configuration (Optional)
      - SENTINEL_DISCORD_WEBHOOK_URL=
      - SENTINEL_TELEGRAM_BOT_TOKEN=
      - SENTINEL_TELEGRAM_CHAT_ID=
      
    volumes:
      - /mnt/user/appdata/torrent-sentinel/vpn_configs:/app/vpn_configs
      - /mnt/user/appdata/torrent-sentinel/data:/app/data
    
    command: ["run"]
```

### Step 3: Launch
```bash
docker compose up -d
```

---

## 3. Direct Python Deployment (Manual / Dev)

1. **Install Poetry**: `curl -sSL https://install.python-poetry.org | python3 -`
2. **Install Dependencies**: `poetry install`
3. **Configure Environment**: Set environment variables (e.g., `export SENTINEL_TRANSMISSION_HOST=...`).
4. **Run the Daemon**:
   ```bash
   poetry run torrent-sentinel run
   ```

---

## 4. Configuration Reference (Environment Variables)

All settings can be overridden using environment variables prefixed with `SENTINEL_`.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `SENTINEL_TRANSMISSION_HOST` | IP/Hostname of Transmission | `localhost` |
| `SENTINEL_TRANSMISSION_PORT` | Transmission RPC Port | `9091` |
| `SENTINEL_TRANSMISSION_RPC_PATH` | Transmission RPC Path | `/transmission/rpc` |
| `SENTINEL_TRANSMISSION_AUTH` | Transmission Auth (`user:pass`) | `None` |
| `SENTINEL_OLLAMA_ENABLED` | Enable LLM diagnostics | `true` |
| `SENTINEL_OLLAMA_BASE_URL` | Ollama API URL | `http://localhost:11434` |
| `SENTINEL_OLLAMA_MODEL` | Ollama Model name | `gemma4:26b` |
| `SENTINEL_VPN_TYPE` | VPN adapter type (`unraid_wireguard`, `mock`) | `unraid_wireguard` |
| `SENTINEL_VPN_INTERFACE` | WireGuard interface name | `wg1` |
| `SENTINEL_VPN_CONFIGS_DIR` | Path to `.conf` profiles | `/app/vpn_configs/` |
| `SENTINEL_VPN_ACTIVE_CONFIG` | Path to active config | `/app/vpn_configs/active.conf` |
| `SENTINEL_DISCORD_WEBHOOK_URL` | Discord webhook for alerts | `None` |
| `SENTINEL_TELEGRAM_BOT_TOKEN` | Telegram Bot API Token | `None` |
| `SENTINEL_TELEGRAM_CHAT_ID` | Telegram Chat ID | `None` |

---

## 5. Troubleshooting

- **Ollama Connection Errors**: Ensure your Ollama container/service is listening on `0.0.0.0` (or the host LAN IP) rather than `127.0.0.1`.
- **WireGuard Capabilities**: If running VPN rotations inside the container, ensure `--cap-add=NET_ADMIN` is passed to the container.
- **Logs**: View real-time logs with `docker logs -f torrent-sentinel`.
