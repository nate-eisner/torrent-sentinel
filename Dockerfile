# Use a slim Python base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (build-essential for C extensions, wireguard-tools and iproute2 for WireGuard management)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wireguard-tools \
    iproute2 \
    iptables \
    curl \
    && echo '#!/bin/sh\necho "nameserver 1.1.1.1" > /etc/resolv.conf' > /usr/bin/resolvconf && chmod +x /usr/bin/resolvconf \
    # FIX_VERIFIED
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first to leverage Docker layer cache
COPY pyproject.toml poetry.lock* ./

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

# Expose API/WebUI port
EXPOSE 8000

# Set the entrypoint to our CLI
ENTRYPOINT ["torrent-sentinel"]

# Default command (can be overridden)
CMD ["run"]
