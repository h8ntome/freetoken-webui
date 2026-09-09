FROM node:22-bookworm-slim AS web-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS python-deps
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY backend/requirements.txt /build/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /build/requirements.txt

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.source="https://github.com/h8ntome/freetoken-webui" \
    org.opencontainers.image.description="FreeToken Web control plane" \
    org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/hf-cache \
    FREETOKEN_MODE=managed \
    FREETOKEN_URL=http://freetoken:1919 \
    FREETOKEN_CONTROL_URL=http://freetoken:1918 \
    MODELS_DIR=/models \
    DATA_DIR=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=python-deps /opt/venv /opt/venv
COPY backend/ /app/backend/
COPY --from=web-build /src/frontend/dist /app/frontend/dist
EXPOSE 3000
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=5 CMD curl -fsS http://127.0.0.1:3000/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "3000", "--proxy-headers", "--forwarded-allow-ips", "*"]
