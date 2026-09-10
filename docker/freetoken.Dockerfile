FROM nvidia/cuda:13.0.2-runtime-ubuntu24.04

ARG UV_VERSION=0.8.22
ARG FREETOKEN_VERSION=0.1.2
ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FREETOKEN_PORT=1919 \
    FREETOKEN_DAEMON_DIR=/state

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-dev python3-pip python3-venv \
        ca-certificates curl tini \
        cuda-nvcc-13-0 \
    && rm -rf /var/lib/apt/lists/*

# FlashInfer compiles GPU-specific kernels on first model load. Keep the CUDA
# compiler and Python/native build headers in the runtime image, then fail the
# image build early if that toolchain is incomplete.
RUN command -v nvcc \
    && command -v c++ \
    && test -f /usr/include/python3.12/Python.h

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv pip install --python /opt/venv/bin/python --no-cache "freetoken[accel]==${FREETOKEN_VERSION}" \
    && /opt/venv/bin/ft --version \
    && /opt/venv/bin/pip uninstall -y uv

# No patched FreeToken code or custom lifecycle server. Upstream owns ft serve.
EXPOSE 1900 1919
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 CMD curl -fsS http://127.0.0.1:1900/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
# Upstream treats an empty env token as enabled auth; omit it when unset.
CMD ["sh", "-c", "if [ -z \"${FREETOKEN_DAEMON_TOKEN:-}\" ]; then unset FREETOKEN_DAEMON_TOKEN; fi; exec ft daemon --host 0.0.0.0 --port 1900 --state-dir /state"]
