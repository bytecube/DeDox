#!/bin/bash
# DeDox + Paperless-ngx - Proxmox LXC Creation Script
#
# Run this script on the Proxmox host to create and bootstrap the DeDox LXC.
# After this script finishes, enter the LXC and run the interactive setup:
#
#   pct enter <VMID>
#   bash /opt/dedox/deploy/proxmox/setup.sh
#
# Usage:
#   bash create-lxc.sh          # Auto-detect next VMID
#   bash create-lxc.sh 110      # Use specific VMID

set -euo pipefail

# --- Configuration (override via environment variables) ---
CT_HOSTNAME="${CT_HOSTNAME:-dedox}"
CT_MEMORY="${CT_MEMORY:-4096}"
CT_SWAP="${CT_SWAP:-1024}"
CT_CORES="${CT_CORES:-2}"
CT_DISK_SIZE="${CT_DISK_SIZE:-64}"
CT_STORAGE="${CT_STORAGE:-local-lvm}"
CT_BRIDGE="${CT_BRIDGE:-vmbr0}"
CT_IP="${CT_IP:-192.168.1.51/24}"
CT_GATEWAY="${CT_GATEWAY:-192.168.1.1}"
CT_NAMESERVER="${CT_NAMESERVER:-192.168.1.1}"

# --- Pre-flight checks ---
if ! command -v pct &> /dev/null; then
    echo "ERROR: 'pct' not found. This script must be run on a Proxmox host."
    exit 1
fi

echo "=== DeDox LXC Creation ==="
echo ""

# --- Get VMID ---
if [ -n "${1:-}" ]; then
    VMID="$1"
    # Check if VMID is already in use
    if pct status "$VMID" &> /dev/null; then
        echo "ERROR: VMID $VMID is already in use."
        pct status "$VMID"
        exit 1
    fi
else
    VMID=$(pvesh get /cluster/nextid 2>/dev/null)
    echo "Auto-detected next available VMID: $VMID"
fi

# --- Download Debian 12 template ---
echo ""
echo "Checking for Debian 12 template..."

# Update template list
pveam update > /dev/null 2>&1 || true

# Find the latest Debian 12 template
TEMPLATE=$(pveam available --section system 2>/dev/null | grep -oP 'debian-12-standard_\S+' | sort -V | tail -1)

if [ -z "$TEMPLATE" ]; then
    echo "ERROR: Could not find Debian 12 template. Trying fallback..."
    TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
fi

# Check if already downloaded
if ! pveam list local 2>/dev/null | grep -q "$TEMPLATE"; then
    echo "Downloading template: $TEMPLATE"
    pveam download local "$TEMPLATE"
else
    echo "Template already available: $TEMPLATE"
fi

TEMPLATE_PATH="local:vztmpl/$TEMPLATE"

# --- Create LXC ---
echo ""
echo "Creating LXC container..."
echo "  VMID:     $VMID"
echo "  Hostname: $CT_HOSTNAME"
echo "  Memory:   ${CT_MEMORY}MB"
echo "  Cores:    $CT_CORES"
echo "  Disk:     ${CT_DISK_SIZE}GB on $CT_STORAGE"
echo "  Network:  $CT_IP via $CT_BRIDGE (gw $CT_GATEWAY)"
echo ""

pct create "$VMID" "$TEMPLATE_PATH" \
    --hostname "$CT_HOSTNAME" \
    --memory "$CT_MEMORY" \
    --swap "$CT_SWAP" \
    --cores "$CT_CORES" \
    --rootfs "${CT_STORAGE}:${CT_DISK_SIZE}" \
    --net0 "name=eth0,bridge=${CT_BRIDGE},ip=${CT_IP},gw=${CT_GATEWAY}" \
    --nameserver "$CT_NAMESERVER" \
    --features nesting=1 \
    --unprivileged 1 \
    --onboot 1 \
    --start 0

echo "LXC container $VMID created."

# --- Start container ---
echo ""
echo "Starting container..."
pct start "$VMID"

# Wait for container to be running
echo "Waiting for container to start..."
for i in $(seq 1 30); do
    if pct status "$VMID" 2>/dev/null | grep -q "running"; then
        break
    fi
    sleep 1
done

# Wait for network
echo "Waiting for network..."
for i in $(seq 1 30); do
    if pct exec "$VMID" -- ping -c 1 -W 2 "$CT_GATEWAY" &> /dev/null; then
        echo "Network is up."
        break
    fi
    sleep 2
done

# --- Bootstrap inside LXC ---
echo ""
echo "Installing Docker and cloning DeDox..."

# Install prerequisites
pct exec "$VMID" -- bash -c '
set -e

# Install basic tools
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git openssl > /dev/null

# Install Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin > /dev/null

echo "Docker installed successfully."

# Clone DeDox
if [ ! -d /opt/dedox ]; then
    git clone --quiet https://github.com/bytecube/DeDox.git /opt/dedox
    echo "DeDox cloned to /opt/dedox"
else
    echo "DeDox already exists at /opt/dedox"
fi
'

# Extract IP without CIDR prefix for display
CT_IP_ADDR="${CT_IP%%/*}"

echo ""
echo "==========================================="
echo "  LXC container $VMID is ready!"
echo "==========================================="
echo ""
echo "Next step: Enter the container and run the interactive setup:"
echo ""
echo "  pct enter $VMID"
echo "  bash /opt/dedox/deploy/proxmox/setup.sh"
echo ""
echo "The setup wizard will guide you through configuring:"
echo "  - DeDox admin credentials"
echo "  - LLM (llama.cpp) connection"
echo "  - Paperless-ngx admin"
echo "  - Open WebUI integration"
echo ""
echo "After setup, services will be available at:"
echo "  - DeDox:      http://${CT_IP_ADDR}:8000"
echo "  - Paperless:  http://${CT_IP_ADDR}:8080"
echo ""
