#!/usr/bin/env bash
# DeDox - One-Line Installer
# Usage: curl -sSL https://raw.githubusercontent.com/bytecube/DeDox/main/setup.sh | bash
#
# Or run directly:
#   bash setup.sh
#
set -euo pipefail

# ─── Colors & Formatting ───────────────────────────────────────────────────────

if [ -t 1 ] || [ -t 2 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' DIM='' RESET=''
fi

# ─── Logging Helpers ───────────────────────────────────────────────────────────

info()    { printf "${BLUE}[i]${RESET} %s\n" "$*"; }
success() { printf "${GREEN}[✓]${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}[!]${RESET} %s\n" "$*"; }
error()   { printf "${RED}[✗]${RESET} %s\n" "$*" >&2; }
step()    { printf "\n${BOLD}${CYAN}── %s ──${RESET}\n\n" "$*"; }

# ─── Interactive Input Helpers ─────────────────────────────────────────────────
# Read from /dev/tty so this works when piped from curl

ask() {
    local prompt="$1"
    local default="${2:-}"
    local response

    if [ -n "$default" ]; then
        printf "${BOLD}%s${RESET} ${DIM}[%s]${RESET}: " "$prompt" "$default" > /dev/tty
    else
        printf "${BOLD}%s${RESET}: " "$prompt" > /dev/tty
    fi

    read -r response < /dev/tty || true
    echo "${response:-$default}"
}

ask_yes_no() {
    local prompt="$1"
    local default="${2:-y}"
    local hint

    if [ "$default" = "y" ]; then
        hint="Y/n"
    else
        hint="y/N"
    fi

    printf "${BOLD}%s${RESET} ${DIM}[%s]${RESET}: " "$prompt" "$hint" > /dev/tty
    local response
    read -r response < /dev/tty || true
    response="${response:-$default}"

    case "$response" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

ask_password() {
    local prompt="$1"
    local min_length="${2:-8}"
    local password

    while true; do
        printf "${BOLD}%s${RESET}: " "$prompt" > /dev/tty
        read -rs password < /dev/tty || true
        printf "\n" > /dev/tty

        if [ ${#password} -lt "$min_length" ]; then
            warn "Password must be at least $min_length characters"
            continue
        fi
        break
    done

    echo "$password"
}

choose() {
    local prompt="$1"
    shift
    local options=("$@")
    local count=${#options[@]}

    printf "${BOLD}%s${RESET}\n" "$prompt" > /dev/tty
    for i in "${!options[@]}"; do
        local num=$((i + 1))
        if [ $num -eq 1 ]; then
            printf "  ${GREEN}[%d]${RESET} %s\n" "$num" "${options[$i]}" > /dev/tty
        else
            printf "  ${DIM}[%d]${RESET} %s\n" "$num" "${options[$i]}" > /dev/tty
        fi
    done

    local selection
    while true; do
        printf "${DIM}Choice [1]:${RESET} " > /dev/tty
        read -r selection < /dev/tty || true
        selection="${selection:-1}"

        if [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "$count" ]; then
            break
        fi
        warn "Please enter a number between 1 and $count"
    done

    echo "$selection"
}

generate_secret() {
    openssl rand -base64 32 2>/dev/null | tr -d '\n' || {
        # Fallback if openssl is not available
        head -c 32 /dev/urandom | base64 | tr -d '\n'
    }
}

# ─── Banner ────────────────────────────────────────────────────────────────────

show_banner() {
    printf "${BOLD}${CYAN}"
    cat << 'BANNER'

    ____       ____
   / __ \___  / __ \____  _  __
  / / / / _ \/ / / / __ \| |/_/
 / /_/ /  __/ /_/ / /_/ />  <
/_____/\___/_____/\____/_/|_|

  Privacy-first document processing

BANNER
    printf "${RESET}"
}

# ─── Prerequisite Checks ──────────────────────────────────────────────────────

COMPOSE_CMD=""

check_prerequisites() {
    step "Checking prerequisites"

    local missing=0

    # Check git
    if command -v git &>/dev/null; then
        success "git $(git --version | awk '{print $3}')"
    else
        error "git is not installed"
        info "  Install: https://git-scm.com/downloads"
        missing=1
    fi

    # Check docker
    if command -v docker &>/dev/null; then
        success "docker $(docker --version | awk '{print $3}' | tr -d ',')"
    else
        error "docker is not installed"
        info "  Install: https://docs.docker.com/get-docker/"
        missing=1
    fi

    # Check docker compose (v2 plugin or standalone)
    if docker compose version &>/dev/null; then
        COMPOSE_CMD="docker compose"
        success "docker compose $(docker compose version --short 2>/dev/null || echo '(v2)')"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
        success "docker-compose $(docker-compose --version | awk '{print $NF}')"
    else
        error "docker compose is not installed"
        info "  Install: https://docs.docker.com/compose/install/"
        missing=1
    fi

    # Check openssl (for secret generation)
    if command -v openssl &>/dev/null; then
        success "openssl available"
    else
        warn "openssl not found — will use /dev/urandom for secret generation"
    fi

    if [ $missing -ne 0 ]; then
        echo
        error "Please install the missing prerequisites and try again."
        exit 1
    fi
}

# ─── GPU Detection ─────────────────────────────────────────────────────────────

HAS_GPU=false

detect_gpu() {
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        HAS_GPU=true
        local gpu_name
        gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")
        success "GPU detected: $gpu_name — Ollama will use GPU acceleration"
    else
        HAS_GPU=false
        info "No NVIDIA GPU detected — Ollama will run on CPU (works fine, just slower)"
    fi
}

# ─── Configuration Wizard ──────────────────────────────────────────────────────

INSTALL_DIR=""
DEPLOY_MODE=""
OLLAMA_MODE=""
OLLAMA_URL=""
OLLAMA_MODEL=""
PAPERLESS_URL=""
PAPERLESS_TOKEN=""
PAPERLESS_USER=""
PAPERLESS_PASS=""
ADMIN_EMAIL=""
ADMIN_PASSWORD=""
OPENWEBUI_ENABLED=""
OPENWEBUI_PORT=""

run_wizard() {

    # ── Install directory ──
    step "Install directory"
    INSTALL_DIR=$(ask "Where should DeDox be installed?" "./dedox")

    # ── Deployment mode ──
    step "Deployment mode"
    local mode_choice
    mode_choice=$(choose "How would you like to deploy DeDox?" \
        "Full stack — includes Paperless-ngx, Ollama, Open WebUI (recommended)" \
        "Minimal — connect to your existing Paperless-ngx instance")

    if [ "$mode_choice" = "1" ]; then
        DEPLOY_MODE="full"
    else
        DEPLOY_MODE="minimal"
    fi

    # ── Ollama setup ──
    step "Ollama (Local LLM)"
    local ollama_choice
    ollama_choice=$(choose "How should Ollama be set up?" \
        "Bundled — include Ollama in the Docker stack (recommended)" \
        "External — connect to an existing Ollama instance")

    if [ "$ollama_choice" = "1" ]; then
        OLLAMA_MODE="bundled"
    else
        OLLAMA_MODE="external"
        OLLAMA_URL=$(ask "Ollama URL" "http://localhost:11434")
        info "Checking connection to $OLLAMA_URL..."
        if curl -sf "$OLLAMA_URL/api/tags" &>/dev/null; then
            success "Connected to Ollama at $OLLAMA_URL"
        else
            warn "Could not reach Ollama at $OLLAMA_URL — make sure it's running when you start DeDox"
        fi
    fi

    # ── LLM model ──
    step "LLM model"
    local model_choice
    model_choice=$(choose "Which model should DeDox use for document analysis?" \
        "qwen3-vl:8b — vision-language, reads documents directly (recommended)" \
        "qwen2.5:14b — text-only, higher quality from OCR text" \
        "Custom model")

    case "$model_choice" in
        1) OLLAMA_MODEL="qwen3-vl:8b" ;;
        2) OLLAMA_MODEL="qwen2.5:14b" ;;
        3) OLLAMA_MODEL=$(ask "Enter model name (e.g., llama3:8b, mistral:7b)") ;;
    esac

    # ── GPU detection ──
    step "GPU detection"
    detect_gpu

    # ── Paperless config (minimal mode) ──
    if [ "$DEPLOY_MODE" = "minimal" ]; then
        step "Paperless-ngx connection"
        PAPERLESS_URL=$(ask "Paperless-ngx URL" "http://localhost:8080")

        if ask_yes_no "Do you have a Paperless API token?" "n"; then
            PAPERLESS_TOKEN=$(ask "API token")
        else
            info "DeDox will auto-generate a token using admin credentials"
            PAPERLESS_USER=$(ask "Paperless admin username" "admin")
            printf "${BOLD}Paperless admin password${RESET}: " > /dev/tty
            read -rs PAPERLESS_PASS < /dev/tty
            printf "\n" > /dev/tty
        fi
    fi

    # ── Admin credentials ──
    step "Admin credentials"
    info "These credentials will be used for DeDox"
    if [ "$DEPLOY_MODE" = "full" ]; then
        info "and shared with bundled Paperless-ngx and Open WebUI"
    fi
    echo

    ADMIN_EMAIL=$(ask "Admin email" "admin@dedox.local")
    ADMIN_PASSWORD=$(ask_password "Admin password (min 8 characters)" 8)

    # ── Open WebUI ──
    step "Open WebUI (RAG document search)"
    local webui_choice
    webui_choice=$(choose "Enable Open WebUI for AI-powered document search?" \
        "Enable — chat with your documents using RAG (recommended)" \
        "Disable — skip Open WebUI")

    if [ "$webui_choice" = "1" ]; then
        OPENWEBUI_ENABLED="true"
        OPENWEBUI_PORT=$(ask "Open WebUI port" "3000")
    else
        OPENWEBUI_ENABLED="false"
    fi
}

# ─── Summary ───────────────────────────────────────────────────────────────────

show_summary() {
    step "Configuration summary"

    local ollama_desc="Bundled"
    if [ "$OLLAMA_MODE" = "external" ]; then
        ollama_desc="External ($OLLAMA_URL)"
    fi
    if [ "$HAS_GPU" = true ] && [ "$OLLAMA_MODE" = "bundled" ]; then
        ollama_desc="$ollama_desc (GPU)"
    elif [ "$OLLAMA_MODE" = "bundled" ]; then
        ollama_desc="$ollama_desc (CPU)"
    fi

    local webui_desc="Disabled"
    if [ "$OPENWEBUI_ENABLED" = "true" ]; then
        webui_desc="Enabled (:$OPENWEBUI_PORT)"
    fi

    local mode_desc="Full stack"
    if [ "$DEPLOY_MODE" = "minimal" ]; then
        mode_desc="Minimal (external Paperless)"
    fi

    printf "\n"
    printf "  ${BOLD}%-14s${RESET} %s\n" "Directory:" "$INSTALL_DIR"
    printf "  ${BOLD}%-14s${RESET} %s\n" "Mode:" "$mode_desc"
    printf "  ${BOLD}%-14s${RESET} %s\n" "Ollama:" "$ollama_desc"
    printf "  ${BOLD}%-14s${RESET} %s\n" "Model:" "$OLLAMA_MODEL"
    printf "  ${BOLD}%-14s${RESET} %s\n" "Open WebUI:" "$webui_desc"
    printf "  ${BOLD}%-14s${RESET} %s\n" "Admin:" "$ADMIN_EMAIL"
    if [ "$DEPLOY_MODE" = "minimal" ]; then
        printf "  ${BOLD}%-14s${RESET} %s\n" "Paperless:" "$PAPERLESS_URL"
    fi
    printf "\n"

    if ! ask_yes_no "Proceed with installation?" "y"; then
        info "Setup cancelled."
        exit 0
    fi
}

# ─── Clone Repository ──────────────────────────────────────────────────────────

clone_repo() {
    step "Cloning DeDox"

    if [ -d "$INSTALL_DIR" ]; then
        if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
            warn "Directory '$INSTALL_DIR' already exists with DeDox files"
            if ! ask_yes_no "Use existing directory?" "y"; then
                info "Setup cancelled."
                exit 0
            fi
            return 0
        else
            warn "Directory '$INSTALL_DIR' already exists"
            if ! ask_yes_no "Continue anyway? Files may be overwritten" "n"; then
                info "Setup cancelled."
                exit 0
            fi
        fi
    fi

    info "Cloning from https://github.com/bytecube/DeDox.git..."
    if git clone https://github.com/bytecube/DeDox.git "$INSTALL_DIR" 2>&1; then
        success "Repository cloned to $INSTALL_DIR"
    else
        error "Failed to clone repository"
        info "Check your network connection and try again"
        exit 1
    fi
}

# ─── Generate .env File ───────────────────────────────────────────────────────

generate_env() {
    step "Generating configuration"

    local env_file="$INSTALL_DIR/.env"

    # Start from template
    cp "$INSTALL_DIR/.env.example" "$env_file"

    # Generate secrets
    local jwt_secret openwebui_secret paperless_secret postgres_pass webhook_secret
    jwt_secret=$(generate_secret)
    openwebui_secret=$(generate_secret)
    paperless_secret=$(generate_secret)
    postgres_pass=$(generate_secret | cut -c1-24)
    webhook_secret=$(generate_secret)

    # Helper to set a value in .env (handles both existing and commented-out keys)
    set_env() {
        local key="$1"
        local value="$2"
        local file="$env_file"

        # Escape special characters for sed
        local escaped_value
        escaped_value=$(printf '%s' "$value" | sed 's/[&/\]/\\&/g')

        if grep -q "^${key}=" "$file" 2>/dev/null; then
            # Key exists uncommented — replace its value
            sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$file"
        elif grep -q "^# *${key}=" "$file" 2>/dev/null; then
            # Key exists but commented — uncomment and set value
            sed -i "s|^# *${key}=.*|${key}=${escaped_value}|" "$file"
        else
            # Key doesn't exist — append it
            echo "${key}=${value}" >> "$file"
        fi
    }

    # Security secrets
    set_env "DEDOX_JWT_SECRET" "$jwt_secret"
    set_env "OPENWEBUI_SECRET_KEY" "$openwebui_secret"
    set_env "PAPERLESS_SECRET_KEY" "$paperless_secret"
    set_env "POSTGRES_PASSWORD" "$postgres_pass"
    set_env "DEDOX_WEBHOOK_SECRET" "$webhook_secret"

    # Admin credentials
    set_env "DEDOX_ADMIN_EMAIL" "$ADMIN_EMAIL"
    set_env "DEDOX_ADMIN_PASSWORD" "$ADMIN_PASSWORD"

    # LLM model
    set_env "OLLAMA_MODEL" "$OLLAMA_MODEL"

    # Shared admin credentials for bundled services
    if [ "$DEPLOY_MODE" = "full" ]; then
        set_env "PAPERLESS_ADMIN_USER" "admin"
        set_env "PAPERLESS_ADMIN_PASSWORD" "$ADMIN_PASSWORD"
    fi

    # Open WebUI
    if [ "$OPENWEBUI_ENABLED" = "true" ]; then
        set_env "OPENWEBUI_PORT" "$OPENWEBUI_PORT"
        set_env "OPENWEBUI_ADMIN_EMAIL" "$ADMIN_EMAIL"
        set_env "OPENWEBUI_ADMIN_PASSWORD" "$ADMIN_PASSWORD"
    fi

    # Minimal mode — external Paperless
    if [ "$DEPLOY_MODE" = "minimal" ]; then
        set_env "DEDOX_PAPERLESS_URL" "$PAPERLESS_URL"
        if [ -n "$PAPERLESS_TOKEN" ]; then
            set_env "DEDOX_PAPERLESS_TOKEN" "$PAPERLESS_TOKEN"
        else
            set_env "PAPERLESS_ADMIN_USER" "$PAPERLESS_USER"
            set_env "PAPERLESS_ADMIN_PASSWORD" "$PAPERLESS_PASS"
        fi
    fi

    # External Ollama
    if [ "$OLLAMA_MODE" = "external" ]; then
        set_env "DEDOX_OLLAMA_URL" "$OLLAMA_URL"
    fi

    success "Configuration saved to $env_file"
    info "All security secrets have been auto-generated"
}

# ─── Generate docker-compose.override.yml ──────────────────────────────────────

generate_override() {
    local override_file="$INSTALL_DIR/docker-compose.override.yml"
    local compose_file

    if [ "$DEPLOY_MODE" = "full" ]; then
        compose_file="docker-compose.yml"
    else
        compose_file="docker-compose.minimal.yml"
    fi

    info "Generating docker-compose.override.yml..."

    # Build the override YAML
    cat > "$override_file" << 'HEADER'
# Auto-generated by DeDox setup script
# Adapts the base compose file to your environment
services:
HEADER

    # ── Ollama overrides ──
    if [ "$OLLAMA_MODE" = "external" ]; then
        cat >> "$override_file" << EOF
  ollama:
    profiles: ["disabled"]
    deploy: {}
  ollama-pull:
    profiles: ["disabled"]
EOF
    elif [ "$HAS_GPU" = false ]; then
        cat >> "$override_file" << 'EOF'
  ollama:
    deploy: {}
EOF
    fi

    # ── Open WebUI overrides ──
    if [ "$OPENWEBUI_ENABLED" = "false" ]; then
        cat >> "$override_file" << 'EOF'
  open-webui:
    profiles: ["disabled"]
EOF
    else
        # Always use non-CUDA image — Open WebUI just calls Ollama API
        if [ "$HAS_GPU" = true ]; then
            cat >> "$override_file" << 'EOF'
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
EOF
        else
            cat >> "$override_file" << 'EOF'
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    deploy: {}
EOF
        fi

        # Point Open WebUI to external Ollama if needed
        if [ "$OLLAMA_MODE" = "external" ]; then
            cat >> "$override_file" << EOF
    environment:
      - OLLAMA_BASE_URL=${OLLAMA_URL}
    depends_on: {}
EOF
        fi
    fi

    # ── DeDox service overrides (depends_on adjustments) ──
    local needs_dedox_override=false
    local dedox_depends=""

    if [ "$DEPLOY_MODE" = "full" ]; then
        # Full mode base depends_on: ollama, paperless, open-webui
        if [ "$OLLAMA_MODE" = "external" ] && [ "$OPENWEBUI_ENABLED" = "false" ]; then
            needs_dedox_override=true
            dedox_depends="      paperless:
        condition: service_healthy"
        elif [ "$OLLAMA_MODE" = "external" ]; then
            needs_dedox_override=true
            dedox_depends="      paperless:
        condition: service_healthy
      open-webui:
        condition: service_healthy"
        elif [ "$OPENWEBUI_ENABLED" = "false" ]; then
            needs_dedox_override=true
            dedox_depends="      ollama:
        condition: service_healthy
      paperless:
        condition: service_healthy"
        fi
    else
        # Minimal mode base depends_on: ollama, open-webui
        if [ "$OLLAMA_MODE" = "external" ] && [ "$OPENWEBUI_ENABLED" = "false" ]; then
            needs_dedox_override=true
            dedox_depends=""
        elif [ "$OLLAMA_MODE" = "external" ]; then
            needs_dedox_override=true
            dedox_depends="      open-webui:
        condition: service_healthy"
        elif [ "$OPENWEBUI_ENABLED" = "false" ]; then
            needs_dedox_override=true
            dedox_depends="      ollama:
        condition: service_healthy"
        fi
    fi

    if [ "$needs_dedox_override" = true ]; then
        if [ -n "$dedox_depends" ]; then
            cat >> "$override_file" << EOF
  dedox:
    depends_on:
${dedox_depends}
EOF
        else
            cat >> "$override_file" << 'EOF'
  dedox:
    depends_on: {}
EOF
        fi
    fi

    # Add external Ollama URL to DeDox services
    if [ "$OLLAMA_MODE" = "external" ]; then
        # Only add environment if we didn't already create the dedox block
        if [ "$needs_dedox_override" = true ]; then
            # Append environment to existing dedox block - need to insert before next service
            sed -i "/^  dedox:$/,/^  [a-z]/ {
                /^    depends_on/i\\    environment:\\n      - DEDOX_OLLAMA_URL=${OLLAMA_URL}
            }" "$override_file" 2>/dev/null || true
        fi

        cat >> "$override_file" << EOF
  dedox-worker:
    environment:
      - DEDOX_OLLAMA_URL=${OLLAMA_URL}
EOF
    fi

    success "Override file generated"
}

# ─── Launch Services ───────────────────────────────────────────────────────────

launch_services() {
    step "Launching DeDox"

    local compose_file
    if [ "$DEPLOY_MODE" = "full" ]; then
        compose_file="docker-compose.yml"
    else
        compose_file="docker-compose.minimal.yml"
    fi

    local compose_args="-f $compose_file"
    if [ -f "$INSTALL_DIR/docker-compose.override.yml" ]; then
        compose_args="$compose_args -f docker-compose.override.yml"
    fi

    info "Building and starting services..."
    info "This may take a few minutes on first run (downloading images & building)..."
    echo

    cd "$INSTALL_DIR"

    # Build and start
    if $COMPOSE_CMD $compose_args up -d --build 2>&1; then
        success "All services started"
    else
        error "Failed to start services"
        info "Check the logs with: cd $INSTALL_DIR && $COMPOSE_CMD $compose_args logs"
        exit 1
    fi
}

# ─── Health Check ──────────────────────────────────────────────────────────────

wait_for_health() {
    step "Waiting for DeDox to be ready"

    local max_wait=180
    local elapsed=0
    local interval=5

    while [ $elapsed -lt $max_wait ]; do
        if curl -sf http://localhost:8000/health &>/dev/null; then
            printf "\r"
            success "DeDox is healthy and ready!"
            return 0
        fi

        printf "\r  ${DIM}Waiting for services to start... (%ds/%ds)${RESET}" "$elapsed" "$max_wait" > /dev/tty
        sleep $interval
        elapsed=$((elapsed + interval))
    done

    printf "\n"
    warn "DeDox didn't respond within ${max_wait}s"
    info "Services may still be starting. Check logs with:"
    info "  cd $INSTALL_DIR && $COMPOSE_CMD logs dedox"
    echo
}

# ─── Final Output ─────────────────────────────────────────────────────────────

show_complete() {
    local compose_file
    if [ "$DEPLOY_MODE" = "full" ]; then
        compose_file="docker-compose.yml"
    else
        compose_file="docker-compose.minimal.yml"
    fi

    local compose_args="-f $compose_file"
    if [ -f "$INSTALL_DIR/docker-compose.override.yml" ]; then
        compose_args="$compose_args -f docker-compose.override.yml"
    fi

    printf "\n"
    printf "${BOLD}${GREEN}"
    cat << 'DONE'
  ╔═════════════════════════════════════════╗
  ║       DeDox is up and running!         ║
  ╚═════════════════════════════════════════╝
DONE
    printf "${RESET}\n"

    printf "  ${BOLD}Access your services:${RESET}\n"
    printf "    DeDox:        ${CYAN}http://localhost:8000${RESET}\n"

    if [ "$DEPLOY_MODE" = "full" ]; then
        printf "    Paperless:    ${CYAN}http://localhost:8080${RESET}\n"
    else
        printf "    Paperless:    ${CYAN}%s${RESET} ${DIM}(external)${RESET}\n" "$PAPERLESS_URL"
    fi

    if [ "$OPENWEBUI_ENABLED" = "true" ]; then
        printf "    Open WebUI:   ${CYAN}http://localhost:%s${RESET}\n" "$OPENWEBUI_PORT"
    fi

    if [ "$OLLAMA_MODE" = "bundled" ]; then
        printf "    Ollama:       ${CYAN}http://localhost:11434${RESET}\n"
    else
        printf "    Ollama:       ${CYAN}%s${RESET} ${DIM}(external)${RESET}\n" "$OLLAMA_URL"
    fi

    printf "\n"
    printf "  ${BOLD}Admin login:${RESET}\n"
    printf "    Email:        %s\n" "$ADMIN_EMAIL"
    printf "    Password:     %s\n" "$(printf '%*s' ${#ADMIN_PASSWORD} '' | tr ' ' '*')"

    printf "\n"
    printf "  ${BOLD}Useful commands:${RESET}\n"
    printf "    ${DIM}cd %s${RESET}\n" "$INSTALL_DIR"
    printf "    ${DIM}$COMPOSE_CMD $compose_args logs -f${RESET}          ${DIM}# View logs${RESET}\n"
    printf "    ${DIM}$COMPOSE_CMD $compose_args down${RESET}             ${DIM}# Stop services${RESET}\n"
    printf "    ${DIM}$COMPOSE_CMD $compose_args up -d${RESET}            ${DIM}# Start again${RESET}\n"

    printf "\n"
    printf "  ${BOLD}Next steps:${RESET}\n"
    if [ "$DEPLOY_MODE" = "full" ]; then
        printf "    1. Open Paperless at ${CYAN}http://localhost:8080${RESET} and upload a document\n"
    else
        printf "    1. Upload a document to your Paperless instance\n"
    fi
    printf "    2. DeDox will automatically process it via webhook\n"
    printf "    3. Check processing status at ${CYAN}http://localhost:8000${RESET}\n"
    if [ "$OPENWEBUI_ENABLED" = "true" ]; then
        printf "    4. Search your documents with AI at ${CYAN}http://localhost:%s${RESET}\n" "$OPENWEBUI_PORT"
    fi

    printf "\n"
}

# ─── Cleanup Trap ──────────────────────────────────────────────────────────────

cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ] && [ $exit_code -ne 130 ]; then
        echo
        error "Setup encountered an error (exit code: $exit_code)"
        info "If you need help, open an issue at: https://github.com/bytecube/DeDox/issues"
    fi
}

trap cleanup EXIT

# ─── Main ──────────────────────────────────────────────────────────────────────

main() {
    show_banner
    check_prerequisites
    run_wizard
    show_summary
    clone_repo
    generate_env
    generate_override
    launch_services
    wait_for_health
    show_complete
}

main "$@"
