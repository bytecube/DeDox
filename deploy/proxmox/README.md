# DeDox - Proxmox LXC Deployment

Deploy DeDox + Paperless-ngx in a Proxmox LXC container, connecting to an **external llama.cpp server** and optionally an **external Open WebUI instance**.

## Architecture

```
┌─────────────────────────────────┐     ┌──────────────────────┐
│     Proxmox LXC Container       │     │  External Services   │
│                                  │     │                      │
│  ┌───────────┐  ┌────────────┐  │     │  ┌────────────────┐  │
│  │ DeDox API │  │  DeDox     │  │────▶│  │  llama.cpp     │  │
│  │ (port 8000)│  │  Worker   │  │     │  │  (port 8080)   │  │
│  └───────────┘  └────────────┘  │     │  └────────────────┘  │
│                                  │     │                      │
│  ┌──────────────────────────┐   │     │  ┌────────────────┐  │
│  │  Paperless-ngx           │   │────▶│  │  Open WebUI    │  │
│  │  (port 8080)             │   │     │  │  (optional)    │  │
│  ├──────────────────────────┤   │     │  └────────────────┘  │
│  │  PostgreSQL  │   Redis   │   │     │                      │
│  └──────────────────────────┘   │     └──────────────────────┘
└─────────────────────────────────┘
```

## Prerequisites

- **Proxmox VE** host with LXC support
- **llama.cpp server** running on your network with OpenAI-compatible API
  - Must be started with `--ctx-size 32768` (or at least 8192)
  - Default 4096 is too small for document extraction prompts
- **Open WebUI** (optional) for RAG-powered document chat

## Quick Start

### Step 1: Create the LXC Container

Run on your **Proxmox host**:

```bash
# Download the script
wget https://raw.githubusercontent.com/bytecube/DeDox/main/deploy/proxmox/create-lxc.sh

# Create LXC with auto-detected VMID
bash create-lxc.sh

# Or specify a VMID
bash create-lxc.sh 110
```

This creates a Debian 12 LXC with Docker installed and the DeDox repository cloned.

#### LXC Configuration

Override defaults via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CT_HOSTNAME` | `dedox` | Container hostname |
| `CT_MEMORY` | `4096` | Memory in MB |
| `CT_SWAP` | `1024` | Swap in MB |
| `CT_CORES` | `2` | CPU cores |
| `CT_DISK_SIZE` | `64` | Disk size in GB |
| `CT_STORAGE` | `local-lvm` | Proxmox storage pool |
| `CT_BRIDGE` | `vmbr0` | Network bridge |
| `CT_IP` | `192.168.1.51/24` | Static IP address |
| `CT_GATEWAY` | `192.168.1.1` | Network gateway |

### Step 2: Run the Setup Wizard

Enter the LXC and run the interactive setup:

```bash
pct enter <VMID>
bash /opt/dedox/deploy/proxmox/setup.sh
```

The wizard will:

1. **Auto-generate security secrets** (JWT, Paperless)
2. **Detect your llama.cpp server** and list available models
3. **Configure Paperless-ngx** admin credentials
4. **Connect to Open WebUI** (optional)
5. **Create `.env` file** with your configuration
6. **Optionally start all services** immediately

### Step 3: Access Services

After startup:

- **DeDox**: `http://<LXC-IP>:8000`
- **Paperless-ngx**: `http://<LXC-IP>:8080`

## Configuration

All configuration is in `deploy/proxmox/.env`. Key sections:

### LLM (llama.cpp)

```bash
DEDOX_LLM_PROVIDER=openai-compat
DEDOX_OLLAMA_URL=http://192.168.1.50:8080
OLLAMA_MODEL=qwen3.5-35b-a3b-q4.gguf
```

Verify your llama.cpp server is working:
```bash
# List available models
curl http://192.168.1.50:8080/v1/models

# Check server properties (including context size)
curl http://192.168.1.50:8080/props
```

### Paperless-ngx

```bash
PAPERLESS_ADMIN_USER=admin
PAPERLESS_ADMIN_PASSWORD=your-password
POSTGRES_PASSWORD=paperless
```

### Open WebUI (Optional)

```bash
DEDOX_OPENWEBUI_URL=http://192.168.1.50:3000
OPENWEBUI_ADMIN_EMAIL=admin@example.com
OPENWEBUI_ADMIN_PASSWORD=your-password
```

## Managing Services

```bash
cd /opt/dedox/deploy/proxmox

# Start all services
docker compose up -d

# View logs
docker compose logs -f dedox-app
docker compose logs -f dedox-worker
docker compose logs -f paperless

# Restart after config changes
docker compose down && docker compose up -d

# Rebuild after code updates
docker compose build && docker compose up -d
```

## Troubleshooting

### LLM Context Size Error

```
request (4288 tokens) exceeds the available context size (4096 tokens)
```

Restart your llama.cpp server with a larger context:
```bash
./llama-server -m model.gguf --ctx-size 32768
```

### Empty LLM Responses

If using Qwen3 thinking models, ensure `disable_thinking: true` is set in `config/settings.yaml`. The thinking tokens (`<think>...</think>`) consume the entire context without producing output.

### Paperless Not Starting

Wait 1-2 minutes for initial database migration. Check logs:
```bash
docker compose logs paperless
```

### Cannot Connect to llama.cpp

Verify network connectivity from the LXC:
```bash
curl http://192.168.1.50:8080/v1/models
```

If it fails, check that the llama.cpp server is listening on all interfaces (`--host 0.0.0.0`), not just localhost.
