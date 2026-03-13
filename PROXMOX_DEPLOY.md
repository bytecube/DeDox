# Deploying DeDox on Proxmox (External Ollama)

This guide covers deploying DeDox in a Proxmox LXC container, connecting to an existing Ollama instance on the same host.

## Architecture

```
Proxmox Host (192.168.1.38)
├── LXC: Ollama (already running, port 11434)
└── LXC: DeDox (this setup)
    ├── DeDox API         :8000
    ├── DeDox Worker
    ├── Paperless-ngx     :8080
    ├── PostgreSQL
    ├── Redis
    └── Open WebUI        :3000
```

## 1. Create the LXC Container

In the Proxmox web UI or via CLI on the host:

```bash
pct create <VMID> local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst \
  --hostname dedox \
  --memory 8192 \
  --swap 2048 \
  --cores 4 \
  --rootfs local-lvm:60 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --features nesting=1 \
  --unprivileged 1 \
  --start 1
```

**Important**: `nesting=1` is required for Docker to work inside the LXC.

Recommended specs:
- **RAM**: 8 GB minimum (Ollama runs elsewhere, so this is enough)
- **Disk**: 60+ GB (Paperless stores documents here)
- **Cores**: 4+

## 2. Install Docker

```bash
pct enter <VMID>

apt update && apt install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | \
  gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

Verify: `docker run --rm hello-world`

## 3. Clone and Configure

```bash
cd /opt
git clone https://github.com/bytecube/DeDox.git dedox
cd dedox

# Create .env from the Proxmox template
cp .env.proxmox .env

# Generate all secrets in one go
sed -i "s|DEDOX_JWT_SECRET=.*|DEDOX_JWT_SECRET=$(openssl rand -base64 32)|" .env
sed -i "s|PAPERLESS_SECRET_KEY=.*|PAPERLESS_SECRET_KEY=$(openssl rand -base64 32)|" .env
sed -i "s|OPENWEBUI_SECRET_KEY=.*|OPENWEBUI_SECRET_KEY=$(openssl rand -base64 32)|" .env
sed -i "s|POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -base64 32)|" .env
```

Edit `.env` to set your admin passwords:
```bash
nano .env
```

## 4. Pull the LLM Model

Make sure your Ollama instance has the required model:

```bash
curl http://192.168.1.38:11434/api/tags  # check what's available

# If you need to pull it:
curl -X POST http://192.168.1.38:11434/api/pull -d '{"name": "qwen3-vl:8b"}'
```

## 5. Start DeDox

```bash
docker compose -f docker-compose.proxmox.yml up -d
```

First run will:
1. Build the DeDox image (takes a few minutes)
2. Pull Paperless-ngx, PostgreSQL, Redis, Open WebUI images
3. Auto-create the DeDox admin user
4. Auto-create Paperless API token
5. Auto-create Open WebUI API key
6. Auto-setup Paperless workflows and custom fields

Monitor startup:
```bash
docker compose -f docker-compose.proxmox.yml logs -f
```

## 6. Verify

```bash
# All containers healthy
docker compose -f docker-compose.proxmox.yml ps

# DeDox API
curl http://localhost:8000/health

# Paperless-ngx
curl -s http://localhost:8080 | head -5

# Open WebUI
curl -s http://localhost:3000/health

# Ollama reachable from DeDox container
docker exec dedox curl -s http://192.168.1.38:11434/api/tags | head
```

## Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| DeDox Dashboard | `http://<container-ip>:8000` | Main UI |
| DeDox API Docs | `http://<container-ip>:8000/docs` | Swagger UI |
| Paperless-ngx | `http://<container-ip>:8080` | Document archive |
| Open WebUI | `http://<container-ip>:3000` | RAG chat interface |

## Updating

```bash
cd /opt/dedox
git pull
docker compose -f docker-compose.proxmox.yml up -d --build
```

## Troubleshooting

### DeDox can't reach Ollama

From inside the DeDox container, verify connectivity:
```bash
docker exec dedox curl http://192.168.1.38:11434/api/tags
```

If it fails, check that:
1. Ollama is bound to `0.0.0.0` (not just `127.0.0.1`)
2. No firewall rules blocking inter-container traffic on the Proxmox bridge
3. The IP `192.168.1.38` is correct: `ip addr show` on the Ollama LXC

### Paperless won't start

Check PostgreSQL health first:
```bash
docker compose -f docker-compose.proxmox.yml logs postgres
```

### Slow LLM responses

The default model `qwen3-vl:8b` is a vision-language model. If your Ollama host has limited resources, try a smaller model:
```bash
# In .env, change:
OLLAMA_MODEL=qwen2.5:7b
```

### Backup

Key data to back up:
- `/opt/dedox/data/` - DeDox database and processed files
- Docker volumes: `docker volume ls | grep dedox` - Paperless documents, PostgreSQL data
