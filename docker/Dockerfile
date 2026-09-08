FROM node:22-bookworm-slim AS web-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nvidia/cuda:13.0.2-devel-ubuntu24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/hf-cache \
    FREETOKEN_MODE=managed \
    FREETOKEN_HOST=127.0.0.1 \
    FREETOKEN_PORT=1919 \
    MODELS_DIR=/models \
    DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv tini ca-certificates curl git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python3 -m pip install --break-system-packages -r /app/backend/requirements.txt \
    "freetoken[accel] @ git+https://github.com/FlashML-org/FreeToken.git@af71ba43206e124f5ff6419b47ee36c6e9981078"
COPY backend/ /app/backend/
COPY --from=web-build /src/frontend/dist /app/frontend/dist
EXPOSE 3000 1919
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS http://127.0.0.1:3000/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "3000", "--proxy-headers", "--forwarded-allow-ips", "*"]
