# FreeToken Web

A self-hosted web control plane for [FreeToken](https://github.com/FlashML-org/FreeToken): model discovery and downloads, exact-process lifecycle management, streaming chat, persistent conversations, real runtime metrics, GPU/system monitoring, logs, and copy-ready native API setup.

FreeToken remains the inference engine. This project does not implement model inference or substitute an API-compatibility shim.

## Quick start

Prerequisites: Ubuntu x86_64, an NVIDIA Ampere-or-newer GPU, driver r580+ (CUDA 13), Docker Engine with Compose v2, and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

```bash
git clone <this-repository-url> freetoken-web
cd freetoken-web
cp .env.example .env
mkdir -p models data hf-cache
```

Set a strong password in `.env`:

```bash
openssl rand -base64 24   # use as ADMIN_PASSWORD
```

Then start it:

```bash
docker compose up -d --build
docker compose ps
```

Open `http://SERVER_IP:3000`. The native FreeToken inference API is published on port `1919`.

Verify GPU visibility inside the same runtime environment:

```bash
docker compose exec freetoken-web nvidia-smi
docker compose exec freetoken-web ft --version
```

The web-container health check measures the control plane only. It remains healthy when no model is intentionally loaded; engine state is shown separately in the UI and at `/api/engine/status`.

## What it manages

- Models: scans complete local checkpoints, detects missing shards, distinguishes verified/likely/unknown compatibility, downloads from Hugging Face with progress/cancel/retry, and validates deletion paths.
- Runtime: launches one exact `ft serve` child with argv (never a shell string), waits for FreeToken `/health`, reads `/v1/stats`, detects exits, preserves logs, and gracefully stops the owned process group.
- Chat: proxies FreeToken's native streaming chat endpoint without buffering, renders Markdown/code, supports reasoning output, and persists chat history in SQLite.
- Monitoring: CPU, RAM, disk and NVML GPU metrics plus actual FreeToken throughput, request latency, VRAM and cache-pool data.
- API: shows the active served-model name and native OpenAI Responses/chat and Anthropic Messages endpoints.

## Managed and external modes

`FREETOKEN_MODE=managed` is the complete experience. The backend launches `ft serve`, owns that exact process, and exposes Load, Switch, Restart and Unload. If port 1919 is already occupied at startup, it refuses to take over or kill the process and enters an unowned external state.

`FREETOKEN_MODE=external` connects to `FREETOKEN_EXTERNAL_URL`. Chat, metrics and API information work when reachable; process lifecycle and model deletion of a running checkpoint are intentionally unavailable. On Linux Docker, add this if the external engine is on the host:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Do not run a managed container against a separately managed `ft serve` on the same published port.

## Storage and existing models

The default bind mounts are:

| Host | Container | Purpose |
|---|---|---|
| `./models` | `/models` | model checkpoints |
| `./data` | `/data` | SQLite, lifecycle state, logs |
| `./hf-cache` | `/hf-cache` | Hugging Face cache |

Point `MODELS_PATH` at an existing directory to scan without redownloading. A directory is not considered ready solely because it exists: it needs `config.json` plus weight files (or a supported GGUF), and every shard referenced by a safetensors index must exist.

Back up `DATA_PATH` for conversations/settings and `MODELS_PATH` for models. Never run `docker compose down -v` as a substitute for understanding your mount configuration.

## Hugging Face

Set `HF_TOKEN` for gated/private checkpoints after accepting the repository's terms on Hugging Face. The token is read only by the backend. Downloads use `huggingface_hub` file APIs, store directly in a FreeToken-loadable local directory, validate free space, expose byte progress/speed/ETA, and remain resumable through Hugging Face's local metadata.

Catalog labels are deliberately conservative:

- **Verified**: listed as known-good in current FreeToken `docs/models.md` at the synchronized upstream commit.
- **Likely**: local architecture appears supported, but the exact checkpoint is not explicitly listed.
- **Unknown**: no compatibility claim is made.

The catalog source is isolated in `backend/app/services/library.py` so it can be updated against upstream without rebuilding UI components.

## Authentication and network exposure

Management authentication defaults to enabled. The password from the environment is Argon2-hashed in memory; only hashed session identifiers are persisted. Cookies are HttpOnly/SameSite=Strict and state changes require a per-session CSRF token.

For HTTPS behind a reverse proxy, set `SECURE_COOKIES=true` and configure `PUBLIC_API_BASE_URL` to the actual externally reachable inference origin. A browser-derived URL is explicitly shown as an unverified candidate—it is not proof of outside-in connectivity.

The management UI (3000) and native inference API (1919) have different security properties. FreeToken's inference API may not require a key. Restrict both to a trusted LAN/VPN or add authentication at a reverse proxy. Do not expose management publicly with `AUTH_ENABLED=false`.

No Docker socket is mounted. There is no arbitrary shell endpoint.

## Configuration

See the comments in `.env.example`. Common values:

- `FREETOKEN_MODE`: `managed` or `external`.
- `FREETOKEN_EXTERNAL_URL`: external engine origin.
- `PUBLIC_API_BASE_URL`: address client applications should use.
- `FREETOKEN_EXTRA_ARGS`: administrator-controlled static args appended to every managed launch. Do not put browser/user input here.
Per-load UI options map only to current documented FreeToken flags: GPU, concurrency, output/sequence/prefill limits, memory ratio, MoE backend, KV tokens, and MoE cache rate. Auto is preferred.

## Updating

Persistent data lives outside the image, so updates are routine:

```bash
git pull
docker compose build --pull
docker compose up -d
```

## Logs and troubleshooting

```bash
docker compose logs -f freetoken-web
docker compose exec freetoken-web nvidia-smi
curl -fsS http://127.0.0.1:3000/healthz
curl -fsS http://127.0.0.1:1919/health
```

Common failures are surfaced with the relevant FreeToken log line in the UI. Check, in order: driver 580+, container GPU visibility, available host RAM/VRAM, model completeness, accepted gated-model terms, disk space, and port 1919 ownership. FreeToken kernels compile on first use with `nvcc`; the first launch can be substantially slower.

## Uninstall

Keep all models and state:

```bash
docker compose down
```

Remove the application and chat state but keep models:

```bash
docker compose down
rm -rf ./data ./hf-cache
```

Full removal is destructive. Only after verifying `MODELS_PATH`, remove that exact model directory yourself; the project intentionally does not provide a broad deletion command.

## Development and tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
(cd backend && ../.venv/bin/pytest -q)

(cd frontend && npm ci && npm test && npm run build)
```

The test suite covers model discovery/completeness, path traversal and symlink deletion, lifecycle locking/argv construction/log bounds, interrupted-job reconciliation, credential hashing/session creation, and core frontend states. Full GPU inference validation must be run on the target NVIDIA host with a compatible checkpoint; it cannot be truthfully substituted by a mock on non-NVIDIA development machines.

## Upstream compatibility basis

The initial implementation was researched against FreeToken commit `af71ba43206e124f5ff6419b47ee36c6e9981078` (2026-09-08) and its current `README.md`, `docs/install.md`, `docs/cli.md`, `docs/models.md`, server control API, stats schema, cache API and daemon lifecycle implementation. Recheck upstream flags and schemas when changing the pinned FreeToken version.
