# Torrent Sentinel

An intelligent daemon and management service designed to monitor Transmission torrent clients, detect stalled downloads caused by VPN tracker blocking/shadow-banning, automatically rotate through VPN server locations (e.g., Surfshark WireGuard), and re-announce torrents to discover healthy peer swarms.

## 🚀 Key Features

- **Local AI Diagnosis**: Uses a local [Ollama](https://ollama.com/) instance to analyze tracker error messages and recommend optimal geographic regions for rotation.
- **Autom: VPN Rotation**: Seamlessly rotates through WireGuard profiles on Unraid to bypass IP bans.
- **Intelligent Decision Engine**: Uses a historical "scoreboard" (SQLite) to track which locations provide the best peer counts and download speeds.
- **Web Dashboard**: A modern, real-time dashboard to monitor torrent health, view rotation history, and manually trigger rotations.
- **Notifications**: Integrated support for Discord and Telegram alerts.

---

## 🖥️ Web Dashboard

The service includes a built-in web interface accessible via the FastAPI backend.

### Features:
- **Real-time Monitoring**: Live view of active torrents, download speeds, and peer counts.
- **System Status**: Quick glance at daemon health and current VPN location.
- **Rotation History**: A timeline of all automated and manual rotations, including the AI's reasoning.
- **Manual Control**: One-click button to trigger an immediate VPN rotation.

**Accessing the UI:**
Once deployed, navigate to `http://<your-server-ip>:8000/` (or the port you configured).

---

## 🛠️ Installation & Deployment

### 1. Docker Deployment (Recommended for Unraid)

The easiest way to run Torrent Sentinel is via Docker Compose.

**Prerequisites:**
- Docker and Docker Compose installed.
- Surfshark WireGuard `.conf` files placed in your appdata directory.

**Step 1: Create `docker-compose.yml`**
```yaml
version: '3.8'

services:
  torrent-sentinel:
    build: .
    container_name: torrent-sentinel
    restart: unless-stopped
    environment:
      - SENTINEL_TRANSMISSION_HOST=192.168.0.X
      - SENTINEL_TRANSMISSION_PORT=9091
      - SENTINEL_OLLAMA_ENABLED=true
      - SENTINEL_OLLAMA_BASE_URL=http://192.168.0.10:11434
      - SENTINEL_OLLAMA_MODEL=gemma4:26b
      - SENTINEL_VPN_TYPE=unraid_wireguard
      - SENTINEL_VPN_INTERFACE=wg1
      - SENTINEL_VPN_CONFIGS_DIR=/app/vpn_configs/
    volumes:
      - /mnt/user/appdata/torrent-sentinel/vpn_configs:/app/vpn_configs
      - /mnt/user/appdata/torrent-sentinel/data:/app/data
    command: ["run"]
```

**Step 2: Deploy**
```bash
docker compose up -d --build
```

### 2. Manual Python Deployment

1. **Install Dependencies**:
   ```bash
   poetry install
   ```
2. **Run the Daemon**:
   ```bash
   poetry run python -m torrent_sentinel.cli run
   ```
3. **Run the Web API**:
   ```bash
   poetry run uvicorn torrent_sentinel.api.router:app --host 0.0.0.0 --port 8000
   ```

---

## ⚙️ Configuration

All settings are managed via environment variables prefixed with `SENTINEL_`.

| Variable | Description | Default |
| :--- | :--- | :--- |
| `SENTINEL_TRANSMISSION_HOST` | IP/Hostname of Transmission | `localhost` |
| `SENTINEL_OLLAMA_BASE_URL` | URL of your Ollama instance | `http://localhost:11434` |
| `SENTINEL_OLLAMA_MODEL` | Model name for diagnosis | `llama3.2` |
| `SENTINEL_VPN_TYPE` | VPN adapter (`unraid_wireguard`, `mock`) | `unraid_wireguard` |
| `SENTINEL_VPN_INTERFACE` | WireGuard interface name | `wg1` |
| `SENTINEL_DISCORD_WEBHOOK_URL` | Discord webhook for alerts | `None` |

---

## 🧪 Testing

Run the core unit tests using pytest:
```bash
poetry run pytest tests/test_core.py
```

## 📄 License
MIT
