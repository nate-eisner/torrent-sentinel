# Deployment Guide: Torrent Sentinel (VPN Gateway)

This document outlines deployment procedures for **Torrent Sentinel** as an automated WireGuard VPN Gateway and torrent health monitor on **Unraid** or via **Docker Compose**.

---

## 🏗️ Architecture: VPN Gateway Container

Torrent Sentinel operates as a dedicated **VPN Gateway container** (similar to Gluetun). 

```
[ Local LAN / Web Browser ]
      │ (Ports 8000 & 9091 via LAN bypass)
      ▼
┌─────────────────────────────────────────────────────────────┐
│  torrent-sentinel (Gateway Container)                      │
│                                                             │
│  - Runs WireGuard Tunnel (wg-quick)                        │
│  - Exposes Ports: 8000 (Sentinel UI), 9091, 51413 (TCP/UDP)│
│  - Sentinel Daemon & Ollama Diagnostics                     │
│  - LAN WebUI Bypass Routing (SENTINEL_LAN_NETWORK)          │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ transmission (Client Container)                       │  │
│  │                                                       │  │
│  │ - Joined via network: container:torrent-sentinel      │  │
│  │ - Talks to Sentinel via localhost (127.0.0.1:9091)    │  │
│  │ - 100% of swarm traffic exits via active WireGuard    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
      │
      ▼ (Encrypted WireGuard VPN)
[ VPN Server / Internet Swarm ]
```

- **Protected Swarm Traffic**: All Transmission traffic routes through the WireGuard VPN tunnel managed by Torrent Sentinel.
- **Automated Rotation**: When stalled torrents are detected, Sentinel rotates its internal WireGuard tunnel and re-announces torrents. Transmission seamlessly shifts to the new VPN IP without restarting.
- **LAN WebUI Access**: With `SENTINEL_LAN_NETWORK` configured, both Sentinel's WebUI (`:8000`) and Transmission's WebUI (`:9091`) remain directly accessible from your local network.

---

## 1. Unraid Deployment (Recommended)

### Step 1: Install Torrent Sentinel
1. Copy or download [`torrent-sentinel.xml`](./torrent-sentinel.xml) to your Unraid flash drive:
   ```text
   /boot/config/plugins/dockerMan/templates-user/torrent-sentinel.xml
   ```
   *(Or add the repository template URL in Docker > Template Repositories).*
2. In the Unraid WebUI, go to **Docker** > **Add Container**, select **torrent-sentinel**.
3. Place your WireGuard `.conf` profiles in `/mnt/user/appdata/torrent-sentinel/vpn_configs/`.
4. Configure template parameters:
   - **LAN Subnets Bypass** (`SENTINEL_LAN_NETWORK`): Set to your home subnet (e.g. `192.168.1.0/24` or default `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12`).
   - **Transmission Host** (`SENTINEL_TRANSMISSION_HOST`): Keep default `127.0.0.1`.
   - **Ollama Base URL** (`SENTINEL_OLLAMA_BASE_URL`): `http://<unraid-ip>:11434`.
5. Click **Apply** to launch Torrent Sentinel.

### Step 2: Attach Transmission to the Sentinel Network
In your Transmission container settings on Unraid:
1. Set **Network Type** to `None`.
2. Scroll to the bottom and click **Show more settings...** (or Advanced View).
3. In **Extra Parameters**, enter:
   ```text
   --net=container:torrent-sentinel
   ```
4. **Remove existing host port allocations** (e.g. 9091 and 51413) from the Transmission template, because all incoming ports are already published and forwarded by `torrent-sentinel`.
5. Click **Apply**.

### Step 3: Verify
- **Torrent Sentinel Dashboard**: `http://<unraid-ip>:8000/`
- **Transmission WebUI**: `http://<unraid-ip>:9091/`
- Check `docker logs torrent-sentinel` to verify that WireGuard initialized and Transmission connected via `127.0.0.1:9091`.

---

## 2. Docker Compose Deployment

### Step 1: Directory Setup
```text
mkdir -p ./vpn_configs ./data
# Copy your WireGuard provider .conf files into ./vpn_configs/
```

### Step 2: `docker-compose.yml`
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
      - "8000:8000"       # Torrent Sentinel WebUI & API
      - "9091:9091"       # Transmission WebUI
      - "51413:51413/tcp" # Torrent Peer Traffic (TCP)
      - "51413:51413/udp" # Torrent Peer Traffic (UDP)
    environment:
      # Transmission Connection (localhost in shared namespace)
      - SENTINEL_TRANSMISSION_HOST=127.0.0.1
      - SENTINEL_TRANSMISSION_PORT=9091
      - SENTINEL_TRANSMISSION_RPC_PATH=/transmission/rpc
      - SENTINEL_TRANSMISSION_AUTH=            # user:password if configured

      # WireGuard VPN Gateway
      - SENTINEL_VPN_TYPE=wireguard
      - SENTINEL_VPN_CONFIGS_DIR=/app/vpn_configs/
      - SENTINEL_VPN_ACTIVE_CONFIG=/app/vpn_configs/active.conf
      
      # LAN Access (allow browser access to ports 8000 & 9091 without VPN routing)
      - SENTINEL_LAN_NETWORK=192.168.0.0/16,10.0.0.0/8,172.16.0.0/12

      # Ollama Diagnostics
      - SENTINEL_OLLAMA_ENABLED=true
      - SENTINEL_OLLAMA_BASE_URL=http://192.168.1.10:11434
      - SENTINEL_OLLAMA_MODEL=gemma4:26b

      # Torrent Boosting & Rescue
      - SENTINEL_BOOST_ENABLED=true
      - SENTINEL_AUTO_VPN_ROTATION_ENABLED=true
      - SENTINEL_AUTO_FAILOVER_ENABLED=false
      - SENTINEL_STALL_THRESHOLD_MINUTES=5
      - SENTINEL_RESCUE_GRACE_PERIOD_MINUTES=60
      - SENTINEL_AUTO_BOOST_CADENCE_MINUTES=120

      # Optional Servarr Integration
      - SENTINEL_SONARR_URL=
      - SENTINEL_SONARR_API_KEY=
      - SENTINEL_RADARR_URL=
      - SENTINEL_RADARR_API_KEY=
      - SENTINEL_LIDARR_URL=
      - SENTINEL_LIDARR_API_KEY=

      # Notifications (Optional)
      - SENTINEL_DISCORD_WEBHOOK_URL=
      - SENTINEL_TELEGRAM_BOT_TOKEN=
      - SENTINEL_TELEGRAM_CHAT_ID=
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

### Step 3: Launch
```bash
docker compose up -d
```

---

## 3. Configuration Reference

All options can be configured via environment variables prefixed with `SENTINEL_`:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `SENTINEL_TRANSMISSION_HOST` | Transmission IP (`127.0.0.1` when sharing network) | `127.0.0.1` |
| `SENTINEL_TRANSMISSION_PORT` | Transmission RPC Port | `9091` |
| `SENTINEL_TRANSMISSION_RPC_PATH` | Transmission RPC Path | `/transmission/rpc` |
| `SENTINEL_TRANSMISSION_AUTH` | Transmission Auth (`user:pass`) | `None` |
| `SENTINEL_LAN_NETWORK` | LAN CIDR subnets allowed to bypass VPN for WebUI | `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12` |
| `SENTINEL_VPN_TYPE` | VPN adapter type (`wireguard`, `mock`) | `wireguard` |
| `SENTINEL_VPN_CONFIGS_DIR` | Directory containing WireGuard `.conf` files | `/app/vpn_configs/` |
| `SENTINEL_VPN_ACTIVE_CONFIG` | Destination path to copy active profile | `/app/vpn_configs/active.conf` |
| `SENTINEL_OLLAMA_ENABLED` | Enable local LLM diagnosis | `true` |
| `SENTINEL_OLLAMA_BASE_URL` | Ollama API endpoint | `http://192.168.0.10:11434` |
| `SENTINEL_OLLAMA_MODEL` | Ollama model name | `gemma4:26b` |
| `SENTINEL_MIN_SEEDS` | Minimum seeds before flagged as stalled | `2` |
| `SENTINEL_MIN_DOWNLOAD_RATE_KBPS` | Minimum rate (KB/s) before diagnosing | `15.0` |
| `SENTINEL_BOOST_ENABLED` | Enable individual torrent tracker injection & boosting | `true` |
| `SENTINEL_AUTO_VPN_ROTATION_ENABLED` | Enable LLM-judged automatic VPN rotation | `true` |
| `SENTINEL_STALL_THRESHOLD_MINUTES`| Minutes stalled before Stage 1 tracker injection | `5` |
| `SENTINEL_RESCUE_GRACE_PERIOD_MINUTES`| Probation grace period (minutes) before Stage 2 failover | `60` |
| `SENTINEL_AUTO_FAILOVER_ENABLED` | Automatically blocklist & search replacement in Servarr | `false` |
| `SENTINEL_AUTO_BOOST_CADENCE_MINUTES`| Recurring tracker re-boost interval (minutes, 0 = disabled) | `120` |
| `SENTINEL_SONARR_URL` | URL to Sonarr (e.g. `http://192.168.1.100:8989`) | `None` |
| `SENTINEL_SONARR_API_KEY` | Sonarr API Key | `None` |
| `SENTINEL_RADARR_URL` | URL to Radarr (e.g. `http://192.168.1.100:7878`) | `None` |
| `SENTINEL_RADARR_API_KEY` | Radarr API Key | `None` |
| `SENTINEL_LIDARR_URL` | URL to Lidarr (e.g. `http://192.168.1.100:8686`) | `None` |
| `SENTINEL_LIDARR_API_KEY` | Lidarr API Key | `None` |
| `SENTINEL_ROTATION_COOLDOWN_MINUTES`| Cooldown between rotations | `15` |
| `SENTINEL_POST_ROTATION_GRACE_PERIOD_MINUTES`| Grace period after rotation to measure recovery | `3` |
| `SENTINEL_MAX_ROTATIONS_PER_HOUR` | Max allowed rotations per hour | `4` |
| `SENTINEL_DISCORD_WEBHOOK_URL` | Discord webhook URL | `None` |
| `SENTINEL_TELEGRAM_BOT_TOKEN` | Telegram Bot API Token | `None` |
| `SENTINEL_TELEGRAM_CHAT_ID` | Telegram Chat ID | `None` |
| `SENTINEL_LOG_LEVEL` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

---

## 4. Troubleshooting

- **Cannot access WebUI (`:8000` or `:9091`)**: Verify `SENTINEL_LAN_NETWORK` contains your home LAN subnet (e.g. `192.168.1.0/24`). Without this bypass, return traffic from the WebUI is swallowed by the VPN tunnel.
- **WireGuard Permissions**: Ensure the `torrent-sentinel` container has `cap_add: - NET_ADMIN` and `sysctl: net.ipv4.conf.all.src_valid_mark=1`.
- **Transmission Connection Refused**: Verify Transmission has started and is attached to `--net=container:torrent-sentinel` (or `network_mode: service:torrent-sentinel`), and that `SENTINEL_TRANSMISSION_HOST=127.0.0.1`.

