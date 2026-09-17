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

An intelligent WireGuard VPN Gateway and management service designed to monitor Transmission torrent clients, route swarm traffic through WireGuard, detect stalled downloads caused by VPN tracker blocking or shadow-banning, automatically rotate through WireGuard VPN server locations (e.g., Surfshark), verify public IP transitions, and re-announce torrents to discover healthy peer swarms.

---

## 🚀 Key Features

- **VPN Gateway Container**: Acts as a dedicated WireGuard gateway for Transmission (`--net=container:torrent-sentinel`), isolating and protecting 100% of swarm traffic.
- **Local AI Diagnosis**: Uses a local [Ollama](https://ollama.com/) instance to analyze tracker errors, stalled states, and recommend optimal regions for rotation.
- **Automated VPN Rotation**: Seamlessly rotates through WireGuard profiles to bypass throttled or blocked endpoints without restarting Transmission.
- **LAN WebUI Bypass**: Built-in policy routing ensures both Torrent Sentinel (`:8000`) and Transmission (`:9091`) WebUIs remain accessible from your home network.
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

Torrent Sentinel operates as a **VPN Gateway container**. Transmission routes its entire network through Torrent Sentinel.

### 1. Unraid Deployment (Recommended for Unraid)

Torrent Sentinel provides a native Unraid template: [`torrent-sentinel.xml`](./torrent-sentinel.xml).

#### Step 1: Install Torrent Sentinel
1. Copy or download [`torrent-sentinel.xml`](./torrent-sentinel.xml) to your Unraid flash drive:
   ```text
   /boot/config/plugins/dockerMan/templates-user/torrent-sentinel.xml
   ```
2. Open the Unraid WebUI, navigate to the **Docker** tab, and click **Add Container**.
3. In the **Template** dropdown, select **torrent-sentinel**.
4. The template will automatically pre-populate:
   - **Container Image**: `ghcr.io/nate-eisner/torrent-sentinel:latest`
   - **Published Ports**: `8000` (Sentinel WebUI), `9091` (Transmission WebUI), `51413` (Torrent Peers TCP/UDP)
   - **App Data Directory**: `/mnt/user/appdata/torrent-sentinel/data` &rarr; `/app/data` (for `sentinel.db`)
   - **VPN Configs Directory**: `/mnt/user/appdata/torrent-sentinel/vpn_configs` &rarr; `/app/vpn_configs` (place `.conf` files here)
   - **LAN Subnets Bypass**: `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12` (allows LAN access to WebUIs)
   - **Extra Parameters**: `--cap-add=NET_ADMIN --sysctl net.ipv4.conf.all.src_valid_mark=1`
5. Configure your **Ollama URL** (`SENTINEL_OLLAMA_BASE_URL`), leave `SENTINEL_TRANSMISSION_HOST=127.0.0.1`, and click **Apply**.

#### Step 2: Configure Transmission Container on Unraid
1. Edit your existing Transmission container (or add one).
2. Set **Network Type** to **None**.
3. Click **Show more settings...** (or switch to Advanced View).
4. In **Extra Parameters**, enter:
   ```text
   --net=container:torrent-sentinel
   ```
5. Remove any port mappings from the Transmission template (ports are published by `torrent-sentinel`).
6. Click **Apply**. Transmission is now fully routed and protected by Torrent Sentinel.

---

### 2. Docker Compose

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
      - "8000:8000"       # Torrent Sentinel WebUI
      - "9091:9091"       # Transmission WebUI
      - "51413:51413/tcp" # Torrent Peer Traffic (TCP)
      - "51413:51413/udp" # Torrent Peer Traffic (UDP)
    environment:
      - SENTINEL_TRANSMISSION_HOST=127.0.0.1
      - SENTINEL_TRANSMISSION_PORT=9091
      - SENTINEL_VPN_TYPE=wireguard
      - SENTINEL_VPN_CONFIGS_DIR=/app/vpn_configs/
      - SENTINEL_LAN_NETWORK=192.168.0.0/16,10.0.0.0/8,172.16.0.0/12
      - SENTINEL_OLLAMA_ENABLED=true
      - SENTINEL_OLLAMA_BASE_URL=http://192.168.1.10:11434
      - SENTINEL_OLLAMA_MODEL=gemma4:26b
    volumes:
      - ./vpn_configs:/app/vpn_configs
      - ./data:/app/data
    command: ["run"]

  transmission:
    image: lscr.io/linuxserver/transmission:latest
    container_name: transmission
    restart: unless-stopped
    network_mode: "service:torrent-sentinel"
    depends_on:
      - torrent-sentinel
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    volumes:
      - ./transmission/config:/config
      - ./transmission/downloads:/downloads
      - ./transmission/watch:/watch
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
| `SENTINEL_TRANSMISSION_HOST` | IP/Hostname of Transmission RPC | `127.0.0.1` |
| `SENTINEL_TRANSMISSION_PORT` | Port of Transmission RPC | `9091` |
| `SENTINEL_TRANSMISSION_RPC_PATH` | Path of Transmission RPC | `/transmission/rpc` |
| `SENTINEL_TRANSMISSION_AUTH` | Credentials (`username:password`) | `None` |
| `SENTINEL_LAN_NETWORK` | Subnets allowed to bypass VPN for WebUI access | `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12` |
| `SENTINEL_OLLAMA_ENABLED` | Enable LLM-based diagnostics | `true` |
| `SENTINEL_OLLAMA_BASE_URL` | URL of your Ollama instance | `http://localhost:11434` |
| `SENTINEL_OLLAMA_MODEL` | Model name for diagnosis | `gemma4:26b` |
| `SENTINEL_OLLAMA_TIMEOUT` | Ollama HTTP timeout (seconds) | `300` |
| `SENTINEL_VPN_TYPE` | VPN adapter (`wireguard`, `mock`) | `wireguard` |
| `SENTINEL_VPN_INTERFACE` | WireGuard interface name | `wg1` |
| `SENTINEL_VPN_CONFIGS_DIR` | Directory containing `.conf` profiles | `/app/vpn_configs/` |
| `SENTINEL_VPN_ACTIVE_CONFIG` | Path to current active config file | `/app/vpn_configs/active.conf` |
| `SENTINEL_MIN_SEEDS` | Minimum seeds before flagged as stalled | `2` |
| `SENTINEL_MIN_DOWNLOAD_RATE_KBPS` | Minimum rate (KB/s) before diagnosing | `15.0` |
| `SENTINEL_STALLED_DURATION_MINUTES`| Minutes stalled before action | `5` |
| `SENTINEL_ROTATION_COOLDOWN_MINUTES`| Cooldown between rotations | `15` |
| `SENTINEL_POST_ROTATION_GRACE_PERIOD_MINUTES`| Grace period after rotation | `3` |
| `SENTINEL_MAX_ROTATIONS_PER_HOUR` | Maximum rotations per hour | `4` |
| `SENTINEL_DISCORD_WEBHOOK_URL` | Discord webhook for notifications | `None` |
| `SENTINEL_TELEGRAM_BOT_TOKEN` | Telegram bot API token | `None` |
| `SENTINEL_TELEGRAM_CHAT_ID` | Telegram chat ID for alerts | `None` |
| `SENTINEL_LOG_LEVEL` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

---

## 🧪 Testing

Run the test suite with pytest:
```bash
poetry run pytest
```

---

## 📄 License

Distributed under the [MIT License](./LICENSE).
