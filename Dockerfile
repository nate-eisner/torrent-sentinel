# Use a slim Python base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies:
# - build-essential for C-based Python packages
# - wireguard-tools, iproute2, iptables for WireGuard management
# - procps for sysctl utility
# - openresolv for WireGuard DNS management (resolvconf)
# - curl for healthchecks/utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wireguard-tools \
    iproute2 \
    iptables \
    procps \
    openresolv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a robust sysctl wrapper in /usr/local/bin so wg-quick never fails
# when setting net.ipv4.conf.all.src_valid_mark in Docker environments
RUN printf '#!/bin/sh\nif [ -x /sbin/sysctl ]; then\n    /sbin/sysctl "$@" 2>/dev/null || {\n        case "$*" in\n            *src_valid_mark*) exit 0 ;;\n            *) exit $? ;;\n        esac\n    }\nelse\n    exit 0\nfi\n' > /usr/local/bin/sysctl \
    && chmod +x /usr/local/bin/sysctl

# Copy dependency files first to leverage Docker layer cache
COPY pyproject.toml poetry.lock ./

# Install Poetry and dependencies (skipping root package until source is copied)
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction --no-ansi

# Copy the rest of the application
COPY . .

# Install the application root package
RUN poetry install --no-interaction --no-ansi

# Create necessary directories for appdata/logs
RUN mkdir -p /app/logs /app/data /app/vpn_configs

# Expose Torrent Sentinel WebUI (8000), Transmission WebUI (9091), and Torrent Peer ports (51413)
EXPOSE 8000 9091 51413/tcp 51413/udp

# Set the entrypoint directly to our CLI
ENTRYPOINT ["torrent-sentinel"]

# Default command (can be overridden)
CMD ["run"]
