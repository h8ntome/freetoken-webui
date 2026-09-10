# Verification

Run the repository checks from a clean checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest backend/tests -q
npm ci --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
docker compose config -q
docker build --pull -f docker/Dockerfile -t freetoken-webui:test .
docker build --pull -f docker/freetoken.Dockerfile -t freetoken-upstream:test .
```

The engine image build verifies that `nvcc`, a C++ compiler, Python headers, and
the CUDA library headers supplied with the accelerator wheels are present.
Those are runtime requirements because FlashInfer can JIT-compile GPU-specific
kernels when a model is first loaded or first used for sampling.

## Live NVIDIA acceptance test

On a compatible Linux/NVIDIA host, use a fresh checkout and the documented
installation:

```bash
cp .env.example .env
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:3000/healthz
curl -fsS http://127.0.0.1:3000/readyz
```

Both services must remain healthy. `/readyz` must report `daemonReachable: true`
in managed mode. Continue through the WebUI with a checkpoint that fits the
host: Discover, Download, Rescan, Load, send a short chat request, and Unload.
The first load can take longer while kernels compile. Confirm that the native
inference endpoint on port 1919 and the WebUI chat stream both return generated
text, then inspect both service logs for crashes, permission errors, or repeated
restarts.

Do not treat an idle daemon or passing unit tests as inference validation. Model
compatibility and memory requirements vary by checkpoint, GPU, and quantization.
