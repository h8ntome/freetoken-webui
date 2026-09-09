FROM node:22-bookworm-slim AS web-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nvidia/cuda:13.0.2-devel-ubuntu24.04 AS runtime
ARG UV_VERSION=0.8.22
ARG FREETOKEN_VERSION=0.1.2
LABEL org.opencontainers.image.source="https://github.com/h8ntome/freetoken-webui" \
      org.opencontainers.image.description="Web control plane for FreeToken local inference" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/hf-cache \
    FREETOKEN_MODE=managed \
    FREETOKEN_HOST=127.0.0.1 \
    FREETOKEN_PORT=1919 \
    MODELS_DIR=/models \
    DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv tini ca-certificates curl git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv pip install --python /opt/venv/bin/python --no-cache \
       -r /app/backend/requirements.txt "freetoken==${FREETOKEN_VERSION}"
# Keep accelerator wheels in their own layer: GHCR rejects individual layers
# larger than 10 GB, while the complete CUDA/PyTorch stack can exceed that.
RUN uv pip install --python /opt/venv/bin/python --no-cache \
       "freetoken[accel]==${FREETOKEN_VERSION}" \
    && ft --version
COPY backend/ /app/backend/
COPY --from=web-build /src/frontend/dist /app/frontend/dist
EXPOSE 3000 1919
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS http://127.0.0.1:3000/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "3000", "--proxy-headers", "--forwarded-allow-ips", "*"]
