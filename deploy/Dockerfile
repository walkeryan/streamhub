# syntax=docker/dockerfile:1.7
#
# StreamHub single-image build. Build from the repository root:
#   docker build -f deploy/Dockerfile -t streamhub .

# ---------------------------------------------------------------------------
# Stage 1: build the web client (Vite) into web-app/dist
# ---------------------------------------------------------------------------
FROM node:22-alpine AS web-build

WORKDIR /src/web-app

# Dependency layer first so it caches across source edits.
COPY web-app/package.json web-app/package-lock.json ./
# Optional "ca" secret: trust a TLS-intercepting proxy in sandboxed builders (no-op otherwise).
RUN --mount=type=secret,id=ca,target=/run/secrets/ca,required=false \
    if [ -s /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi \
    && npm ci

# The web build reads ../design (tokens.json) and may reference ../contracts,
# so keep the repository layout intact inside the builder.
COPY design /src/design
COPY contracts /src/contracts
COPY web-app/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/walkeryan/streamhub" \
      org.opencontainers.image.title="StreamHub" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PUID=1000 \
    PGID=1000 \
    TZ=Etc/UTC \
    LIBVA_DRIVER_NAME=iHD \
    FORWARDED_ALLOW_IPS=127.0.0.1

# ffmpeg for remux/transcode; Intel iHD VA-API driver (non-free) and vainfo
# for hardware decode/encode. tini reaps children and forwards signals;
# gosu drops privileges after the entrypoint has set up file ownership.
# The Debian base image ships only the "main" component, so enable
# contrib/non-free for the Intel driver (handles both the DEB822 and the
# legacy sources.list layouts).
# hadolint ignore=DL3008
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i -E 's/^(deb[^ ]* [^ ]+ [^ ]+) main$/\1 main contrib non-free non-free-firmware/' \
            /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
        gosu \
        tzdata \
        ffmpeg \
        intel-media-va-driver-non-free \
        vainfo; \
    rm -rf /var/lib/apt/lists/*

# Non-root application user. IDs are remapped to PUID/PGID by the entrypoint.
RUN groupadd --system --gid 1000 streamhub \
    && useradd --system --uid 1000 --gid streamhub \
        --home-dir /config --no-create-home --shell /usr/sbin/nologin streamhub \
    && mkdir -p /config /transcode \
    && chown streamhub:streamhub /config /transcode

WORKDIR /app

# Backend: install the hub-server package and its dependencies.
COPY hub-server/ /app/
# The optional "ca" secret lets corporate/sandboxed builders trust a TLS-intercepting
# proxy; it is absent (and harmless) in normal builds and CI.
RUN --mount=type=secret,id=ca,target=/run/secrets/ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi \
    && pip install /app \
    && rm -rf /app/build /app/*.egg-info

# Frontend build output, served by the API at /.
COPY --from=web-build /src/web-app/dist /app/web-dist

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

VOLUME ["/config"]
EXPOSE 8787

# Liveness: the API must answer on its health route (no auth required).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/v1/health', timeout=4).status == 200 else 1)"]

# Single worker: the stream proxy and sync scheduler keep in-process state.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers", "--workers", "1"]
