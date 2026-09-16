<p align="center">
  <img src="assets/logo.png" alt="Torrent Sentinel Logo" width="220" />
</p>

<h1 align="center">Torrent Sentinel</h1>

<p align="center">
  <strong>Automated WireGuard VPN Rotation & Torrent Health Monitor with Local AI Diagnostics & Web Dashboard</strong>
</p>

<p align="center">
  <a href="https://github.com/nate-eisner/torrent-sentinel/actions/workflows/docker-publish.yml">
    <img src="https://github.com/nate-eisner/torrent-sentinel/actions/workflows/docker-publish.yml/badge.svg" alt="Build and Publish Docker Image" />
  </a>
  <a href="https://ghcr.io/nate-eisner/torrent-sentinel">
    <img src="https://img.shields.io/badge/container-ghcr.io-blue.svg" alt="GitHub Container Registry" />
  </a>
  <a href="https://github.com/nate-eisner/torrent-sentinel/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License" />
  </a>
  <a href="https://python-poetry.org/">
    <img src="https://img.shields.io/badge/packaging-poetry-purple.svg" alt="Poetry" />
  </a>
</p>

---

An intelligent daemon and management service designed to monitor Transmission torrent clients, detect stalled downloads caused by VPN tracker blocking or shadow-banning, automatically rotate through WireGuard VPN server locations (e.g., Surfshark), verify public IP transitions, and re-announce torrents to discover healthy peer swarms.

---

## 🚀 Key Features

- **Local AI Diagnosis**: Uses a local [Ollama](https://ollama.com/) instance to analyze tracker errors, stalled states, and recommend optimal regions for rotation.
- **Automated VPN Rotation**: Seamlessly rotates through WireGuard profiles (`wg1`) on Unraid to bypass throttled or blocked endpoints.
- **Intelligent Decision Engine**: Uses a historical "scoreboard" (SQLite) to track which locations provide the best peer counts and download speeds.
- **Web Dashboard**: Modern, real-time web UI to monitor torrent health, view active transfers, review rotation timelines, and manually trigger rotations.
- **Anti-Flapping & IP Verification**: Built-in cooldowns, grace periods, and hourly caps prevent tunnel flapping while verifying public IP changes.
- **Notifications**: Integrated support for Discord webhooks and Telegram alerts.
- **Unraid Ready**: Native Unraid Docker XML template with pre-configured volume paths, WebUI integration, and network parameters.

---

## 🖥️ Web Dashboard

The service includes a built-in real-time dashboard accessible via the FastAPI backend:

- **Real-time Monitoring**: Live view of active torrents, transfer speeds, and connected peers.
- **System Status**: Quick glance at daemon health and current active VPN location.
- **Rotation History**: Chronological timeline of all automated and manual rotations, including AI reasoning and peer deltas.
- **Manual Control**: One-click button to trigger an immediate VPN rotation to the next best location.

**Accessing the UI:**
Navigate to `http://<your-server-ip>:8000/` (or your configured port).

---

## 🛠️ Installation & Deployment

### 1. Unraid Docker Template (Recommended for Unraid)

Torrent Sentinel provides a native Unraid template: [`torrent-sentinel.xml`](./torrent-sentinel.xml).

#### Method A: Direct USB Flash Installation
1. Copy or download [`torrent-sentinel.xml`](./torrent-sentinel.xml) to your Unraid flash drive:
   ```text
   /boot/config/plugins/dockerMan/templates-user/torrent-sentinel.xml
   ```
2. Open the Unraid WebUI, navigate to the **Docker** tab, and click **Add Container**.
3. In the **Template** dropdown, select **torrent-sentinel**.
4. The template will automatically pre-populate:
   - **Container Image**: `ghcr.io/nate-eisner/torrent-sentinel:latest`
   - **WebUI Port**: `8000` (click the container icon &rarr; **WebUI**)
   - **App Data Directory**: `/mnt/user/appdata/torrent-sentinel/data` &rarr; `/app/data` (for `sentinel.db`)
   - **VPN Configs Directory**: `/mnt/user/appdata/torrent-sentinel/vpn_configs` &rarr; `/app/vpn_configs`
   - **Extra Parameters**: `--cap-add=NET_ADMIN` (required for WireGuard tunnel switching)
5. Fill in your **Transmission IP** (`SENTINEL_TRANSMISSION_HOST`) and **Ollama URL** (`SENTINEL_OLLAMA_BASE_URL`), then click **Apply**.

#### Method B: Template Repository URL
1. In the Unraid WebUI, go to the **Docker** tab, scroll to **Template repositories**, and add:
   ```text
   https://raw.githubusercontent.com/nate-eisner/torrent-sentinel/main/torrent-sentinel.xml
   ```
2. Click **Save**, then click **Add Container** and select **torrent-sentinel**.

---

### 2. Docker Compose

If using Docker Compose (or Dockge / Docker Compose Manager on Unraid):

**Step 1: Directory Setup**
```text
/mnt/user/appdata/torrent-sentinel/
├── vpn_configs/      <-- Place your WireGuard .conf files here
└── data/             <-- Stores sentinel.db SQLite database
```

**Step 2: Create `docker-compose.yml`**
```yaml
version: '3.8'

services:
  torrent-sentinel:
    image: ghcr.io/nate-eisner/torrent-sentinel:latest
    container_name: torrent-sentinel
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
    ports:
      - "8000:8000"
    environment:
      # Transmission Configuration
      - SENTINEL_TRANSMISSION_HOST=192.168.0.X
      - SENTINEL_TRANSMISSION_PORT=9091
      - SENTINEL_TRANSMISSION_RPC_PATH=/transmission/rpc
      - SENTINEL_TRANSMISSION_AUTH=            # user:password if required
      
      # Ollama AI Configuration
      - SENTINEL_OLLAMA_ENABLED=true
      - SENTINEL_OLLAMA_BASE_URL=http://192.168.0.10:11434
      - SENTINEL_OLLAMA_MODEL=gemma4:26b
      
      # VPN Configuration (Unraid WireGuard)
      - SENTINEL_VPN_TYPE=unraid_wireguard
      - SENTINEL_VPN_INTERFACE=wg1
      - SENTINEL_VPN_CONFIGS_DIR=/app/vpn_configs/
      - SENTINEL_VPN_ACTIVE_CONFIG=/app/vpn_configs/active.conf
      
      # Notifications (Optional)
      - SENTINEL_DISCORD_WEBHOOK_URL=
      - SENTINEL_TELEGRAM_BOT_TOKEN=
      - SENTINEL_TELEGRAM_CHAT_ID=
    volumes:
      - /mnt/user/appdata/torrent-sentinel/vpn_configs:/app/vpn_configs
      - /mnt/user/appdata/torrent-sentinel/data:/app/data
    command: ["run"]
```

**Step 3: Deploy**
```bash
docker compose up -d
```

---

### 3. Manual Python Deployment

1. **Install Dependencies**:
   ```bash
   poetry install
   ```
2. **Run the Daemon**:
   ```bash
   poetry run torrent-sentinel run
   ```
3. **Run the Web API & Dashboard**:
   ```bash
   poetry run uvicorn torrent_sentinel.api.router:app --host 0.0.0.0 --port 8000
   ```
4. **CLI Utilities**:
   ```bash
   poetry run torrent-sentinel history       # View past rotations
   poetry run torrent-sentinel test-ollama   # Test Ollama connectivity
   ```

---

## ⚙️ Configuration Reference

All settings can be configured via environment variables prefixed with `SENTINEL_`:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `SENTINEL_TRANSMISSION_HOST` | IP/Hostname of Transmission RPC | `localhost` |
| `SENTINEL_TRANSMISSION_PORT` | Port of Transmission RPC | `9091` |
| `SENTINEL_TRANSMISSION_RPC_PATH` | Path of Transmission RPC | `/transmission/rpc` |
| `SENTINEL_TRANSMISSION_AUTH` | Credentials (`username:password`) | `None` |
| `SENTINEL_OLLAMA_ENABLED` | Enable LLM-based diagnostics | `true` |
| `SENTINEL_OLLAMA_BASE_URL` | URL of your Ollama instance | `http://localhost:11434` |
| `SENTINEL_OLLAMA_MODEL` | Model name for diagnosis | `gemma4:26b` |
| `SENTINEL_OLLAMA_TIMEOUT` | Ollama HTTP timeout (seconds) | `300` |
| `SENTINEL_VPN_TYPE` | VPN adapter (`unraid_wireguard`, `mock`) | `unraid_wireguard` |
| `SENTINEL_VPN_INTERFACE` | WireGuard interface name | `wg1` |
| `SENTINEL_VPN_CONFIGS_DIR` | Directory containing `.conf` profiles | `/mnt/user/...` |
| `SENTINEL_VPN_ACTIVE_CONFIG` | Path to current active config file | `/.../active.conf` |
| `SENTINEL_MIN_SEEDS` | Minimum seeds before flagged as stalled | `2` |
| `SENTINEL_MIN_DOWNLOAD_RATE_KBPS` | Minimum rate (KB/s) before diagnosing | `15.0` |
| `SENTINEL_STALLED_DURATION_MINUTES`| Minutes stalled before action | `5` |
| `SENTINEL_ROTATION_COOLDOWN_MINUTES`| Cooldown between rotations | `15` |
| `SENTINEL_POST_ROTATION_GRACE_PERIOD_MINUTES`| Grace period after rotation | `3` |
| `SENTINEL_MAX_ROTATIONS_PER_HOUR` | Maximum rotations per hour | `4` |
| `SENTINEL_DISCORD_WEBHOOK_URL` | Discord webhook for notifications | `None` |
| `SENTINEL_TELEGRAM_BOT_TOKEN` | Telegram bot API token | `None` |
| `SENTINEL_TELEGRAM_CHAT_ID` | Telegram chat ID for alerts | `None` |

---

## 🧪 Testing

Run the test suite with pytest:
```bash
poetry run pytest
```

---

## 📄 License

Distributed under the [MIT License](./LICENSE).
