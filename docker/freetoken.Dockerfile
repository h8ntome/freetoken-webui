FROM nvidia/cuda:13.0.2-runtime-ubuntu24.04

ARG UV_VERSION=0.8.22
ARG FREETOKEN_VERSION=0.1.2
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FREETOKEN_PORT=1919 \
    FREETOKEN_CONTROL_PORT=1918

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip python3-venv ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv pip install --python /opt/venv/bin/python --no-cache "freetoken[accel]==${FREETOKEN_VERSION}" \
    && /opt/venv/bin/ft --version \
    && /opt/venv/bin/pip uninstall -y uv

COPY docker/freetoken-entrypoint.py /opt/freetoken-entrypoint.py
EXPOSE 1918 1919
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 CMD curl -fsS http://127.0.0.1:1918/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/freetoken-entrypoint.py"]
