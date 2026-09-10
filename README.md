# FreeToken WebUI

A web interface alongside [upstream FreeToken](https://github.com/FlashML-org/FreeToken).

```text
Browser → WebUI (React + FastAPI, port 3000)
             ├─ model downloads → shared /models
             ├─ lifecycle/logs → upstream ft daemon (private port 1900)
             └─ chat/stats → upstream ft serve (port 1919)
```

The two containers stay separate. The GPU image installs the **unmodified upstream
`freetoken[accel]==0.1.2` package** and runs `ft daemon`. There is no custom process
supervisor, engine fork, Docker socket, or inference reimplementation. The daemon
starts/stops/switches its own engine using its native API. The WebUI owns only its
model downloads, local database, and HTTP client connections.

No official upstream container was found in the upstream installation instructions,
repository, or release workflows during the September 9, 2026 audit. The small
[`docker/freetoken.Dockerfile`](docker/freetoken.Dockerfile) packages the official
PyPI installation without modifying its files. See [verification notes](docs/verification.md).

## Install

Managed GPU service requirements follow upstream: Linux x86-64, supported NVIDIA
GPU (Ampere or newer), driver r580+, Docker Compose 2.30+ with GPU support, and
NVIDIA Container Toolkit. The WebUI container itself needs no GPU or CUDA.

```bash
git clone https://github.com/h8ntome/freetoken-webui.git
cd freetoken-webui
cp .env.example .env
docker compose up -d
```

Open `http://SERVER:3000`, then **Models → Discover → Download → Library → Load**.
The normal Compose file builds both images from this checkout, avoiding old
published images with incompatible control APIs. The initial CUDA/PyTorch build
is large and can take time. It also includes the CUDA compiler and native headers
that FlashInfer needs to JIT-compile GPU-specific kernels on first model load.
Subsequent image builds use Docker's layer cache.

The daemon is healthy while idle; the inference port becomes available after a
model is loaded. `/healthz` checks WebUI liveness. `/readyz` checks daemon
connectivity in managed mode or inference readiness in external mode. A failed
model does not make the WebUI container unhealthy and prevent troubleshooting.

Persistent paths:

| Host setting | Container path | Contents |
| --- | --- | --- |
| `MODELS_PATH=./models` | `/models` in both services | Model files; read-only to the engine |
| `DATA_PATH=./data` | `/data` in WebUI | Chats, sessions, download jobs |
| `FREETOKEN_STATE_PATH=./freetoken-state` | `/state` in daemon | Upstream lifecycle state and engine logs |
| `HF_CACHE_PATH=./hf-cache` | `/hf-cache` | Upstream Hugging Face cache |

Authentication defaults off for a trusted local installation. Set `AUTH_ENABLED=true`
and `ADMIN_PASSWORD` before exposing the WebUI to other users. The native inference
port is a separate service; protect its network access independently. The daemon
port is not published. Set the same non-empty `FREETOKEN_DAEMON_TOKEN` for the WebUI
and daemon to require upstream's `X-FT-Token` header. Tokens stay server-side.

## Connect to existing FreeToken (WebUI only)

```bash
cp .env.example .env
# In .env set FREETOKEN_EXTERNAL_URL to the existing inference address.
docker compose -f docker-compose.external.yml up -d
```

The default address is `http://host.docker.internal:1919`, including a Linux host
gateway mapping. Use a LAN hostname or reachable container address as appropriate.
Set `FREETOKEN_API_KEY` only if the existing inference endpoint requires a bearer token.

External mode supports real chat, native API information, and runtime statistics.
It intentionally disables local downloads, deletion, and lifecycle controls: an
inference endpoint alone does not grant access to its host filesystem or daemon.

To manage an **existing upstream daemon with shared model storage**, deploy only
the WebUI with `FREETOKEN_MODE=managed`, `FREETOKEN_URL`, `FREETOKEN_CONTROL_URL`,
and optionally `FREETOKEN_DAEMON_TOKEN`. Mount its model directory into the WebUI,
set `MODELS_DIR` to that mount, and `FREETOKEN_MODELS_DIR` to the path seen by the
daemon (default `/models`). Both must refer to the same files. Do not start a second
daemon for that instance. Paths registered outside the shared mount also need an
equivalent mount in the engine service; ordinary directories under `/models` are
recommended.

## Models and downloads

- Public Hugging Face repositories use explicit anonymous access without `HF_TOKEN`.
  Blank and whitespace tokens are normalized. Gated/private models require accepted
  Hub terms and a token with access.
- The default catalog/search shows upstream-listed checkpoints supported by the
  pinned runtime. **Verified** means listed upstream, not tested on your GPU.
- **Include unverified results** exposes other repositories with clear labels.
  Architecture matching is exact against the installed release's registry, never
  inferred from a repository name. Unknown and unsupported results cannot be downloaded.
  “Likely” still requires review of the checkpoint's quantization and hardware needs.
- Upstream `main` currently includes architectures absent from the published 0.1.2
  wheel. Those newer models are deliberately excluded. Update the registry/catalog
  together with the runtime version.
- Downloads pin the resolved commit, report bytes/speed/ETA, retry transient failures,
  and preserve `.part` files for **Retry / resume**. Download history survives page
  navigation and WebUI restarts. Interrupted jobs become retryable failures.
- A revision change during resume requires explicit removal of the incomplete model,
  preventing mixed checkpoint revisions. Cancellation can take up to a network read
  timeout while a remote server is stalled. It preserves downloaded files.
- Errors include the underlying cause and operation/repository context in the job,
  Logs page, and backend container output. Secrets and signed query strings are redacted.
- Existing checkpoints are detected from config and weights; indexed shards must all
  exist. Partial downloads, malformed indexes, empty weights, and unsafe paths are
  rejected. `.ftw` with config is recognized; this downloader targets HF safetensors,
  not general GGUF import or checkpoint conversion.
- Delete requires an unloaded checkpoint and a reachable daemon. Active downloads,
  the storage root, path escapes, and linked checkpoint deletion are refused.

## Runtime, logs, and metrics

Load and switch use native `/engine/start` and atomic `/engine/switch`; stop uses
`/engine/stop`. Failed lifecycle mutations are not blindly retried. The WebUI
refreshes authoritative daemon state after restarts and captures real engine logs
from the daemon's SSE stream. Reconnection replays retained logs, so duplicate lines
can appear after daemon/network restarts.

Chat streams directly through to the native OpenAI endpoint, with incremental UTF-8
parsing and persisted partial responses on interruption. Errors are shown in the
stream and logged. Unsupported conversation branching/edit-regenerate controls
were removed rather than leave actions that corrupt persisted history.

Throughput, request latency, cache allocation, GPU identity/capacity, and engine VRAM
come from native runtime stats. Daemon metrics provide engine process RAM/VRAM.
CPU, system RAM, and storage shown by the WebUI belong to the **WebUI host** (container
visibility may differ from the host). Upstream does not expose live GPU utilization,
temperature, or power through these APIs; these are unavailable, not fabricated zeros.

## Configuration

See [`.env.example`](.env.example). Common settings:

| Setting | Purpose |
| --- | --- |
| `WEB_PORT`, `WEB_BIND_ADDRESS` | Published WebUI port/interface |
| `FREETOKEN_PORT`, `API_BIND_ADDRESS` | Published native inference port/interface |
| `FREETOKEN_EXTERNAL_URL` | Existing inference endpoint in external mode |
| `PUBLIC_API_BASE_URL` | Client-reachable inference URL shown in API examples |
| `FREETOKEN_DAEMON_TOKEN` | Native daemon authentication |
| `FREETOKEN_API_KEY` | Existing inference endpoint bearer token |
| `FREETOKEN_EXTRA_ARGS` | Trusted administrator arguments appended to native serve |
| `ENGINE_READY_TIMEOUT_SECONDS` | Readiness deadline; failed model remains inspectable |
| `ENGINE_STOP_TIMEOUT_SECONDS` | WebUI stop-request timeout allowance; upstream owns termination policy |
| `HF_TOKEN` | Optional private/gated repository access |
| `SECURE_COOKIES` | Enable with HTTPS when WebUI authentication is enabled |

Advanced model options are passed as argv values to upstream, including GPU,
`--memory-ratio`, `--moe-backend`, and output/concurrency limits. Unknown options
are rejected. The native API is available at `http://SERVER:1919/v1` after loading;
see the API page for examples. A displayed URL is not proof of outside reachability.

## Updates and migration

```bash
git pull
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 freetoken-webui freetoken
```

Before migrating the old custom-supervisor deployment, unload its model and save
its Compose/configuration for rollback. Replace both images together: the old
port-1918 supervisor is incompatible with the new native port-1900 daemon client.
Keep the same models/data/cache mounts. No database or model deletion is required.
The new daemon needs its additional state mount. Never use `down -v` for updates.

A WebUI restart leaves FreeToken running. A full engine-container restart ends its
processes; upstream records/reports state and does not automatically reload a model
by default. Select Load again. Automatic OOM restart loops are not enabled.

## Development and verification

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest backend/tests -q
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
```

The tests cover native daemon contracts, anonymous downloads, resume, errors,
filesystem boundaries, streaming persistence, authentication, and UI states.
See [docs/verification.md](docs/verification.md) for the exact live checks and
remaining Docker/NVIDIA acceptance tests. Passing unit tests is not GPU validation.

License: Apache-2.0. FreeToken WebUI is an independent interface for upstream FreeToken.
