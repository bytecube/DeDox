#!/bin/bash
# DeDox + Paperless-ngx - Setup & Configuration Script
#
# Run this inside the DeDox LXC container. It will:
# 1. Install Docker and git (if not already installed)
# 2. Clone the DeDox repository
# 3. Walk you through an interactive configuration wizard
# 4. Optionally start the services
#
# Usage:
#   bash /opt/dedox/deploy/proxmox/setup.sh

set -euo pipefail

# --- Helper function ---
prompt() {
    local var_name="$1"
    local prompt_text="$2"
    local default_value="$3"
    local is_secret="${4:-false}"

    if [ "$is_secret" = "true" ] && [ -n "$default_value" ]; then
        # Show masked default for secrets
        printf "  %s [****]: " "$prompt_text"
    elif [ -n "$default_value" ]; then
        printf "  %s [%s]: " "$prompt_text" "$default_value"
    else
        printf "  %s: " "$prompt_text"
    fi

    local input
    read -r input
    printf -v "$var_name" '%s' "${input:-$default_value}"
}

generate_password() {
    openssl rand -base64 16 | tr -d '=/+' | head -c 16
}

echo ""
echo "============================================"
echo "  DeDox + Paperless-ngx Setup Wizard"
echo "============================================"
echo ""

# --- Install Docker ---
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg git openssl > /dev/null
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin > /dev/null
    echo "Docker installed."
else
    echo "Docker already installed."
fi

# Install git and openssl if missing
for pkg in git openssl curl; do
    if ! command -v "$pkg" &> /dev/null; then
        apt-get install -y -qq "$pkg" > /dev/null
    fi
done

# --- Clone DeDox ---
INSTALL_DIR="/opt/dedox"
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Cloning DeDox..."
    git clone --quiet https://github.com/bytecube/DeDox.git "$INSTALL_DIR"
else
    echo "DeDox directory exists at $INSTALL_DIR"
fi

DEPLOY_DIR="$INSTALL_DIR/deploy/proxmox"
cd "$DEPLOY_DIR"

# --- Create data directory ---
mkdir -p ./data

# --- Check for existing .env ---
if [ -f ".env" ]; then
    echo ""
    echo "An existing .env file was found."
    printf "  Overwrite it? [y/N]: "
    read -r overwrite
    if [[ ! "$overwrite" =~ ^[Yy] ]]; then
        echo "Keeping existing .env. Skipping configuration."
        echo ""
        echo "To start services: cd $DEPLOY_DIR && docker compose up -d"
        exit 0
    fi
fi

# --- Detect LXC IP ---
LXC_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "--- Security Secrets ---"
echo ""
DEDOX_JWT_SECRET=$(openssl rand -base64 32)
PAPERLESS_SECRET_KEY=$(openssl rand -base64 32)
echo "  JWT secret:       auto-generated"
echo "  Paperless secret: auto-generated"

echo ""
echo "--- DeDox Admin Account ---"
echo "(Created automatically on first startup)"
echo ""
DEDOX_ADMIN_EMAIL=""
DEDOX_ADMIN_PASSWORD=""
prompt DEDOX_ADMIN_EMAIL "Admin email" "admin@example.com"
prompt DEDOX_ADMIN_PASSWORD "Admin password" "changeme123" true

echo ""
echo "--- LLM Connection (llama.cpp) ---"
echo ""
DEDOX_OLLAMA_URL=""
prompt DEDOX_OLLAMA_URL "llama.cpp server URL" "http://192.168.1.50:8080"

# Try to auto-detect model
DETECTED_MODEL=""
echo ""
echo "  Checking for available models at $DEDOX_OLLAMA_URL..."
MODEL_RESPONSE=$(curl -sf --connect-timeout 5 "$DEDOX_OLLAMA_URL/v1/models" 2>/dev/null || true)
if [ -n "$MODEL_RESPONSE" ]; then
    # Extract first model ID from JSON response
    DETECTED_MODEL=$(echo "$MODEL_RESPONSE" | grep -oP '"id"\s*:\s*"\K[^"]+' | head -1 || true)
    if [ -n "$DETECTED_MODEL" ]; then
        echo "  Found model: $DETECTED_MODEL"
    else
        echo "  Server reachable but no models detected."
    fi
else
    echo "  Could not reach llama.cpp server (will retry when services start)."
fi

OLLAMA_MODEL=""
prompt OLLAMA_MODEL "Model name" "${DETECTED_MODEL:-qwen3.5-35b-a3b-q4.gguf}"

DEDOX_LLM_API_KEY=""
prompt DEDOX_LLM_API_KEY "API key (empty if no auth)" ""

echo ""
echo "--- Paperless-ngx ---"
echo ""
PAPERLESS_ADMIN_USER=""
PAPERLESS_ADMIN_PASSWORD=""
PG_PASSWORD_DEFAULT=$(generate_password)
POSTGRES_PASSWORD=""
prompt PAPERLESS_ADMIN_USER "Paperless admin username" "admin"
prompt PAPERLESS_ADMIN_PASSWORD "Paperless admin password" "$(generate_password)" true
prompt POSTGRES_PASSWORD "PostgreSQL password" "$PG_PASSWORD_DEFAULT" true

PAPERLESS_URL=""
prompt PAPERLESS_URL "Paperless external URL" "http://${LXC_IP}:8080"

echo ""
echo "--- Open WebUI Integration ---"
echo "(Your existing Open WebUI instance for RAG sync)"
echo ""
OPENWEBUI_URL=""
prompt OPENWEBUI_URL "Open WebUI URL" "http://192.168.1.50:3000"

OPENWEBUI_ADMIN_EMAIL=""
OPENWEBUI_ADMIN_PASSWORD=""
DEDOX_OPENWEBUI_API_KEY=""
prompt OPENWEBUI_ADMIN_EMAIL "Open WebUI admin email" "$DEDOX_ADMIN_EMAIL"
prompt OPENWEBUI_ADMIN_PASSWORD "Open WebUI admin password" "" true
prompt DEDOX_OPENWEBUI_API_KEY "Open WebUI API key (empty for auto-generate)" ""

# --- Copy config files ---
if [ ! -d "./config" ]; then
    echo ""
    echo "Copying config files..."
    cp -r "$INSTALL_DIR/config" ./config
fi

# --- Write .env ---
echo ""
echo "Writing .env file..."

cat > .env << ENVEOF
# DeDox + Paperless-ngx - Generated Configuration
# Generated on: $(date -Iseconds)

# =============================================================================
# SECURITY
# =============================================================================
DEDOX_JWT_SECRET=${DEDOX_JWT_SECRET}
PAPERLESS_SECRET_KEY=${PAPERLESS_SECRET_KEY}

# =============================================================================
# DEDOX ADMIN
# =============================================================================
DEDOX_ADMIN_EMAIL=${DEDOX_ADMIN_EMAIL}
DEDOX_ADMIN_PASSWORD=${DEDOX_ADMIN_PASSWORD}

# =============================================================================
# LLM (llama.cpp)
# =============================================================================
DEDOX_LLM_PROVIDER=openai-compat
DEDOX_OLLAMA_URL=${DEDOX_OLLAMA_URL}
OLLAMA_MODEL=${OLLAMA_MODEL}
DEDOX_LLM_API_KEY=${DEDOX_LLM_API_KEY}

# =============================================================================
# PAPERLESS-NGX
# =============================================================================
PAPERLESS_ADMIN_USER=${PAPERLESS_ADMIN_USER}
PAPERLESS_ADMIN_PASSWORD=${PAPERLESS_ADMIN_PASSWORD}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
PAPERLESS_URL=${PAPERLESS_URL}

# =============================================================================
# OPEN WEBUI
# =============================================================================
DEDOX_OPENWEBUI_URL=${OPENWEBUI_URL}
DEDOX_OPENWEBUI_API_KEY=${DEDOX_OPENWEBUI_API_KEY}
OPENWEBUI_ADMIN_EMAIL=${OPENWEBUI_ADMIN_EMAIL}
OPENWEBUI_ADMIN_PASSWORD=${OPENWEBUI_ADMIN_PASSWORD}

# =============================================================================
# WEBHOOKS (optional)
# =============================================================================
DEDOX_WEBHOOK_SECRET=
ENVEOF

echo "Configuration saved to: $DEPLOY_DIR/.env"

# --- Summary ---
echo ""
echo "============================================"
echo "  Configuration Complete"
echo "============================================"
echo ""
echo "  DeDox admin:    $DEDOX_ADMIN_EMAIL"
echo "  LLM server:     $DEDOX_OLLAMA_URL"
echo "  LLM model:      ${OLLAMA_MODEL:-<not set>}"
echo "  Paperless:      $PAPERLESS_URL"
echo "  Open WebUI:     $OPENWEBUI_URL"
echo ""
echo "  DeDox will be at:      http://${LXC_IP}:8000"
echo "  Paperless will be at:  http://${LXC_IP}:8080"
echo ""

# --- Offer to start ---
printf "Start services now? [Y/n]: "
read -r start_now
if [[ ! "$start_now" =~ ^[Nn] ]]; then
    echo ""
    echo "Starting services (this may take a few minutes on first run)..."
    docker compose up -d
    echo ""
    echo "Services are starting. Monitor with:"
    echo "  cd $DEPLOY_DIR && docker compose logs -f"
else
    echo ""
    echo "To start later:"
    echo "  cd $DEPLOY_DIR && docker compose up -d"
fi
echo ""
