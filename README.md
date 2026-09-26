<p align="center">
  <img src="assets/logo.png" alt="Torrent Sentinel Logo" width="220" />
</p>

<h1 align="center">Torrent Sentinel</h1>

<p align="center">
  <strong>Autonomous AI Autopilot, WireGuard VPN Gateway & Torrent Swarm Booster for Transmission & Servarr</strong>
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

**Torrent Sentinel** is an autonomous WireGuard VPN Gateway and intelligent fleet manager designed to monitor Transmission torrent clients, route 100% of swarm traffic through WireGuard tunnels, detect stalled downloads, inject verified public trackers, interactively judge torrent viability with local LLMs ([Ollama](https://ollama.com/)), automate media failover with **Sonarr**, **Radarr**, and **Lidarr**, and dynamically rotate VPN locations when swarms are throttled or shadow-banned.

---

## 🚀 Key Features

- **✈️ Autonomous AI Autopilot (`off` / `advisory` / `full`)**: Complete fleet autopilot engine that evaluates queue health, assesses VPN gateway status, and coordinates precision rescue operations (tracker boosting, DHT re-announces, integrity rechecks, or Servarr failover) with hard deterministic safety guardrails.
- **🤖 Dynamic Runtime Ollama Model Selector**: Query installed models directly from your Ollama host and switch active models on-the-fly via the Web UI header dropdown, REST API, or CLI without restarting containers or modifying environment variables.
- **🔍 Deep Interactive AI Download Judge**: Click "AI Judge" on any torrent to perform deep diagnostic inspection—providing an expert diagnostic verdict, viability score (0–100%), recommended action, and clear plain-English explanation.
- **💬 Conversational Queue Assistant**: Chat directly with Sentinel AI in natural language to query swarm health, diagnose problematic downloads, or inspect VPN routing decisions.
- **⚡ Surgical Torrent Boosting**: Automatically detects stalled or 0-seed downloads and injects fresh, verified public trackers (curated from top-tier tracker repositories) while triggering immediate DHT & tracker re-announces.
- **🛠️ Self-Healing Auto-Repair**: Identifies client-level errors (file corruption, paused errors) and triggers automatic integrity verification (`recheck`) and resume.
- **🔄 Smart Servarr Auto-Failover**: Correlates Transmission torrents with **Sonarr**, **Radarr**, and **Lidarr** queues. Automatically or manually blocklists dead releases and requests fresh alternative searches (`EpisodeSearch`, `MoviesSearch`, `AlbumSearch`).
- **🛡️ Deterministic Safety Guardrails**: Hardcoded safeguards protect private trackers from leakage, enforce minimum confidence floors on failovers, cap failover velocity, and apply anti-flapping cooldowns on VPN rotations.
- **⚡ Context-Optimized Token Efficiency**: Telemetry filtering prunes finished seeders and bounds candidates to top active/stalled downloads, reducing prompt payloads by over 95% for lightning-fast execution and zero context overflow.
- **🔒 Dedicated VPN Gateway**: Functions as a containerized WireGuard network gateway (`--net=container:torrent-sentinel`), shielding 100% of torrent traffic with automated IP leak prevention.
- **🌐 LAN WebUI Bypass**: Policy routing ensures both Torrent Sentinel (`:8000`) and Transmission (`:9091`) WebUIs remain accessible directly from your home network.
- **🔗 Servarr & Web UI Quick Links Bar**: Configurable header shortcuts to seamlessly jump to Transmission, Sonarr, Radarr, Lidarr, Prowlarr, Bazarr, and Readarr.
- **📊 Modern Web Dashboard & Flight Log**: Real-time stats, filterable torrent lists (All, Downloading, Stalled/Errored, Completed), live action buttons, and dual history logs (Flight Log events & VPN Rotations).
- **📢 Rich Notifications**: Instant webhook alerts for Discord and Telegram.
- **📦 Unraid Ready**: Native Unraid Docker XML template with pre-mapped paths, WebUI integration, and network parameters.

---

## ✈️ AI Autopilot & Fleet Management

Torrent Sentinel's **AI Autopilot Engine** brings autonomous decision-making to your download fleet:

```
                      [ Periodic Fleet Telemetry Snapshot ]
                      • Queue Overview (Active, Seeding, Stalled)
                      • Top Problematic & Downloading Candidates
                      • Current WireGuard Gateway Health & History
                                         │
                                         ▼
                            ┌─────────────────────────────┐
                            │      Ollama Local LLM       │
                            │  (Gemma 4, Llama 3, Qwen)   │
                            └──────────────┬──────────────┘
                                         │
                                         ▼
                               [ Proposed Flight Plan ]
                               • Fleet Summary & VPN Verdict
                               • Targeted Torrent Actions
                                         │
                                         ▼
                            ┌─────────────────────────────┐
                            │ Hard Deterministic Guardrails│
                            └──────────────┬──────────────┘
                             ├── Cooldown Active? ➔ Suppress VPN Rotation
                             ├── Low Confidence? ➔ Floor Failover Action
                             ├── Rate Limit Cap? ➔ Limit Failovers / Cycle
                             └── Private Tracker? ➔ Complete Immunity
                                         │
                                         ▼
                            ┌─────────────────────────────┐
                            │    Mode Execution Engine    │
                            └──────────────┬──────────────┘
                             ├── FULL: Execute Actions (Boost, Recheck, Failover, Rotate)
                             └── ADVISORY: Record Simulated Flight Events & Alert
```

### Autopilot Modes
- **`off`**: Autopilot is inactive; standard automated booster and manual controls operate normally.
- **`advisory`**: The AI generates flight plans and evaluates swarm health on each cycle, logging simulated decisions to the Flight Log without mutating torrent states or rotating VPN tunnels.
- **`full`**: Autonomous closed-loop management. The AI executes approved surgical actions and rotates VPN gateways within strict safety guardrails.

---

## 🎯 Stalled Torrent Boosting & Rescue Pipeline

In addition to Autopilot, Torrent Sentinel provides a continuous **two-stage rescue and recovery pipeline**:

```
                  [ Torrent Stalled in Transmission (0 seeds / slow) ]
                                         │
                                         ▼
                            ┌─────────────────────────────┐
                            │ Stage 1: Swarm Booster      │
                            └──────────────┬──────────────┘
                                         │
                          • Injects live, verified public trackers
                          • Forces re-announce to DHT & trackers
                          • Auto-repairs corrupt/errored states (recheck & resume)
                          • Enters probation grace period (default 60m)
                                         │
                                         ▼
                            { Did seeds or speed recover? }
                             ├── YES: 🎉 Swarm Revived! Resumes downloading.
                             └── NO:  (Grace period expires)
                                         │
                                         ▼
                            ┌─────────────────────────────┐
                            │ Stage 2: Servarr Failover   │
                            │ (UI Toggle or Manual Action)│
                            └──────────────┬──────────────┘
                          • Removes dead download from Transmission
                          • Blocklists bad release in Sonarr/Radarr/Lidarr
                          • Dispatches search command for fresh healthy release
```

---

## 🖥️ Web Dashboard

The built-in web dashboard is accessible at `http://<your-server-ip>:8000/`:

- **Header Controls**:
  - **Autopilot Mode**: Instant toggle between `Off`, `Advisory`, and `Full`.
  - **Runtime Model**: Live dropdown to switch active Ollama models on-the-fly.
  - **Auto Failover**: Toggle automatic Servarr blocklisting and replacement search.
  - **VPN Rotation**: Pause or activate automated VPN rotation without stopping the daemon.
  - **Rotate VPN**: One-click manual rotation to the next best WireGuard gateway.
- **Web UIs Bar**: Direct navigation links to your configured Servarr apps and Transmission WebUI.
- **Real-Time Fleet Grid**: Filter torrents by `All`, `Downloading`, `Stalled / Errored`, or `Completed`.
- **Row Actions**:
  - **AI Judge**: Open deep diagnostic assessment modal for individual torrents.
  - **⚡ Boost**: Immediately inject public trackers and re-announce.
  - **🔄 Recheck**: Force client integrity hash-check.
  - **❌ Failover**: Blocklist release in Servarr and trigger replacement search.
- **Flight Log & History**: Real-time event log of AI decisions, executed actions, and VPN location swaps.
- **Chat Assistant**: Interactive slide-out chat drawer to converse with Sentinel AI about your queue.

---

## 🛠️ Installation & Deployment

Torrent Sentinel operates as a **VPN Gateway container**. Transmission routes its entire network through Torrent Sentinel.

### 1. Unraid Deployment (Recommended for Unraid)

Torrent Sentinel includes a native Unraid template: [`torrent-sentinel.xml`](./torrent-sentinel.xml).

#### Step 1: Install Torrent Sentinel
1. Copy [`torrent-sentinel.xml`](./torrent-sentinel.xml) to your Unraid flash drive:
   ```text
   /boot/config/plugins/dockerMan/templates-user/torrent-sentinel.xml
   ```
2. In the Unraid WebUI, navigate to **Docker** &rarr; **Add Container**.
3. Select **torrent-sentinel** from the **Template** dropdown.
4. Verify the pre-configured parameters:
   - **Container Image**: `ghcr.io/nate-eisner/torrent-sentinel:latest`
   - **Published Ports**: `8000` (Sentinel WebUI), `9091` (Transmission WebUI), `51413` (Torrent Peers TCP/UDP)
   - **App Data Directory**: `/mnt/user/appdata/torrent-sentinel/data` &rarr; `/app/data` (stores `sentinel.db`)
   - **VPN Configs Directory**: `/mnt/user/appdata/torrent-sentinel/vpn_configs` &rarr; `/app/vpn_configs` (place `.conf` files here)
   - **LAN Subnets Bypass**: `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12` (LAN access to WebUIs)
   - **Extra Parameters**: `--cap-add=NET_ADMIN --sysctl net.ipv4.conf.all.src_valid_mark=1`
5. Configure your **Ollama URL** (`SENTINEL_OLLAMA_BASE_URL`) and click **Apply**.

#### Step 2: Configure Transmission Container on Unraid
1. Edit your existing Transmission container in Unraid.
2. Set **Network Type** to **None**.
3. In **Extra Parameters** (under Advanced View), enter:
   ```text
   --net=container:torrent-sentinel
   ```
4. Remove any port mappings from Transmission (ports are exposed via `torrent-sentinel`).
5. Click **Apply**.

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
      - SENTINEL_AUTOPILOT_MODE=advisory
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

Deploy with:
```bash
docker compose up -d
```

---

### 3. Command Line Interface (CLI)

Torrent Sentinel includes a complete CLI for inspection, manual runs, and automation:

```bash
# Start Web UI and Daemon
poetry run torrent-sentinel run

# View VPN rotation history
poetry run torrent-sentinel history

# Test Ollama connection & health
poetry run torrent-sentinel test-ollama

# Manage Runtime LLM Model
poetry run torrent-sentinel model status
poetry run torrent-sentinel model set llama3.1:8b

# AI Autopilot Controls
poetry run torrent-sentinel autopilot status
poetry run torrent-sentinel autopilot mode advisory
poetry run torrent-sentinel autopilot run --force --mode full
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
| `SENTINEL_TRANSMISSION_WEB_URL` | Custom WebUI URL override for Transmission | `None` (auto-derived) |
| `SENTINEL_TRANSMISSION_WEB_ENABLED` | Show Transmission WebUI link in dashboard | `true` |
| `SENTINEL_LAN_NETWORK` | Subnets allowed to bypass VPN for WebUI access | `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12` |
| `SENTINEL_OLLAMA_ENABLED` | Enable LLM-based diagnostics & Autopilot | `true` |
| `SENTINEL_OLLAMA_BASE_URL` | URL of your Ollama instance | `http://192.168.0.10:11434` |
| `SENTINEL_OLLAMA_MODEL` | Default fallback model name | `gemma4:26b` |
| `SENTINEL_OLLAMA_TIMEOUT` | Ollama HTTP timeout in seconds | `300` |
| `SENTINEL_OLLAMA_NUM_CTX` | Context window token size (e.g. `32768`, `65536`) | `None` (Ollama default) |
| `SENTINEL_AUTOPILOT_MODE` | Initial Autopilot mode (`off`, `advisory`, `full`) | `off` |
| `SENTINEL_AUTOPILOT_INTERVAL_MINUTES` | Frequency of automated Autopilot cycles | `10` |
| `SENTINEL_AUTOPILOT_MIN_CONFIDENCE_FAILOVER`| Confidence floor required to execute failovers | `0.85` |
| `SENTINEL_AUTOPILOT_MAX_FAILOVERS_PER_CYCLE`| Maximum failover actions per flight cycle | `1` |
| `SENTINEL_AUTOPILOT_ROTATION_COOLDOWN_MINUTES`| Minimum time between Autopilot VPN rotations | `20` |
| `SENTINEL_VPN_TYPE` | VPN adapter (`wireguard`, `mock`) | `wireguard` |
| `SENTINEL_VPN_INTERFACE` | WireGuard interface name | `wg1` |
| `SENTINEL_VPN_CONFIGS_DIR` | Directory containing `.conf` profiles | `/app/vpn_configs/` |
| `SENTINEL_VPN_ACTIVE_CONFIG` | Path to current active config file | `/app/vpn_configs/active.conf` |
| `SENTINEL_MIN_SEEDS` | Minimum seeds before flagged as stalled | `2` |
| `SENTINEL_MIN_DOWNLOAD_RATE_KBPS` | Minimum rate (KB/s) before diagnosing | `15.0` |
| `SENTINEL_BOOST_ENABLED` | Enable individual torrent tracker injection & boosting | `true` |
| `SENTINEL_AUTO_VPN_ROTATION_ENABLED` | Enable automated VPN rotation | `true` |
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
| `SENTINEL_PROWLARR_URL` | URL to Prowlarr (e.g. `http://192.168.1.100:9696`) | `None` |
| `SENTINEL_PROWLARR_API_KEY` | Prowlarr API Key | `None` |
| `SENTINEL_BAZARR_URL` | URL to Bazarr (e.g. `http://192.168.1.100:6767`) | `None` |
| `SENTINEL_BAZARR_API_KEY` | Bazarr API Key | `None` |
| `SENTINEL_READARR_URL` | URL to Readarr (e.g. `http://192.168.1.100:8787`) | `None` |
| `SENTINEL_READARR_API_KEY` | Readarr API Key | `None` |
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
