#!/bin/bash
# DeDox + Paperless-ngx - Proxmox LXC Setup Script
#
# Run this script inside a fresh Debian 12 LXC container on Proxmox.
#
# LXC creation (run on Proxmox host):
#   pct create <VMID> local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
#     --hostname dedox \
#     --memory 4096 \
#     --swap 1024 \
#     --cores 2 \
#     --rootfs local-lvm:16 \
#     --net0 name=eth0,bridge=vmbr0,ip=dhcp \
#     --features nesting=1 \
#     --unprivileged 1
#   pct start <VMID>
#   pct enter <VMID>
#
# Then run: bash setup.sh

set -euo pipefail

echo "=== DeDox + Paperless-ngx Setup ==="
echo ""

# --- Install Docker ---
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    echo "Docker installed."
else
    echo "Docker already installed."
fi

# --- Install git ---
if ! command -v git &> /dev/null; then
    apt-get install -y git
fi

# --- Clone DeDox ---
INSTALL_DIR="/opt/dedox"
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Cloning DeDox..."
    git clone https://github.com/bytecube/DeDox.git "$INSTALL_DIR"
else
    echo "DeDox directory exists, pulling latest..."
    cd "$INSTALL_DIR" && git pull
fi

cd "$INSTALL_DIR/deploy/proxmox"

# --- Copy config files ---
if [ ! -d "./config" ]; then
    echo "Copying config files..."
    cp -r "$INSTALL_DIR/config" ./config

    # Update Open WebUI URL to point to external instance
    sed -i 's|base_url: "http://open-webui:8080"|base_url: "http://192.168.1.50:3000"|' ./config/settings.yaml
fi

# --- Create data directory ---
mkdir -p ./data

# --- Setup .env ---
if [ ! -f ".env" ]; then
    echo "Creating .env from template..."
    cp .env.example .env

    # Generate secrets
    JWT_SECRET=$(openssl rand -base64 32)
    PAPERLESS_SECRET=$(openssl rand -base64 32)
    sed -i "s|DEDOX_JWT_SECRET=CHANGE-ME|DEDOX_JWT_SECRET=$JWT_SECRET|" .env
    sed -i "s|PAPERLESS_SECRET_KEY=CHANGE-ME|PAPERLESS_SECRET_KEY=$PAPERLESS_SECRET|" .env

    # Get this LXC's IP for Paperless URL
    LXC_IP=$(hostname -I | awk '{print $1}')
    sed -i "s|REPLACE-WITH-THIS-LXC-IP|$LXC_IP|" .env

    echo ""
    echo "=== IMPORTANT: Edit .env before starting ==="
    echo ""
    echo "  nano /opt/dedox/deploy/proxmox/.env"
    echo ""
    echo "Check these settings:"
    echo "  - OLLAMA_MODEL: Set to your llama.cpp model name"
    echo "  - DEDOX_ADMIN_PASSWORD: Change the admin password"
    echo "  - PAPERLESS_ADMIN_PASSWORD: Change the Paperless admin password"
    echo "  - OPENWEBUI_ADMIN_PASSWORD: Set if you want RAG sync"
    echo ""
else
    echo ".env already exists, skipping."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env:  nano /opt/dedox/deploy/proxmox/.env"
echo "  2. Start:      cd /opt/dedox/deploy/proxmox && docker compose up -d"
echo "  3. Check:      docker compose logs -f"
echo ""
echo "Services will be available at:"
echo "  - DeDox:      http://$(hostname -I | awk '{print $1}'):8000"
echo "  - Paperless:  http://$(hostname -I | awk '{print $1}'):8080"
echo ""
