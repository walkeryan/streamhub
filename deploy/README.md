# StreamHub deployment

Files in this directory:

| File | Purpose |
| --- | --- |
| `Dockerfile` | Multi-stage image: builds `web-app` with Node 22, runs `hub-server` on Python 3.12 with ffmpeg and VA-API. |
| `entrypoint.sh` | Maps `PUID`/`PGID`, grants `/dev/dri` access, fixes `/config` ownership, sets `TZ`, then drops privileges with `gosu`. |
| `Dockerfile.dockerignore` | Build-context exclusions (BuildKit reads it next to the Dockerfile). |
| `docker-compose.yml` | Single-service compose file. |
| `unraid-template.xml` | Unraid Community Applications template. |

## Build

Run from the repository root. The build context must be the root because the image needs `hub-server/`, `web-app/`, `design/` and `contracts/`.

```bash
docker build -f deploy/Dockerfile -t streamhub .
```

Run the image:

```bash
docker run -d --name streamhub -p 8787:8787 \
  -v "$PWD/config:/config" --tmpfs /transcode \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  streamhub
```

Health: `docker inspect -f '{{.State.Health.Status}}' streamhub` (checks `GET /api/v1/health` every 30 s).

Publish (CI does this on pushes to `main` and on version tags):

```bash
docker build -f deploy/Dockerfile -t ghcr.io/walkeryan/streamhub:latest .
docker push ghcr.io/walkeryan/streamhub:latest
```

## Runtime configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `PUID` / `PGID` | `1000` / `1000` | Numeric IDs the app runs as. Unraid usually uses `99` / `100`. |
| `TZ` | `Etc/UTC` | Applied at start, for example `America/New_York`. |
| `SECRET_KEY` | none | Required. Signs tokens and encrypts source credentials. |
| `TMDB_API_KEY` | none | Optional. Can also be set in the UI. |
| `ADMIN_PASSWORD` | none | Optional. Otherwise set on first run. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy addresses allowed to set `X-Forwarded-*`. |

Volumes: `/config` (persistent state, back it up) and `/transcode` (scratch, use tmpfs).
Optional device: `/dev/dri` for VA-API. The entrypoint adds the app user to the render node's group automatically.
Port: `8787`.

If the container is started with `--user`, the entrypoint skips ID remapping and runs the command as that user directly.

## Verification

- `hadolint deploy/Dockerfile` is clean (DL3008 for unpinned apt packages is suppressed inline with a reason).
- `docker compose -f deploy/docker-compose.yml config` validates with `SECRET_KEY` set.
- `unraid-template.xml` is well-formed XML.
