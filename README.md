# StreamHub

A self-hosted, unified streaming hub. One Docker container serves the API and the web client; a native Android TV app talks to the same API. Everything runs on your server: clients never contact Plex, Jellyfin, IPTV providers or TMDB directly.

> Legal notice: StreamHub is a media player and organizer. It does not provide any channels, playlists, subscriptions, EPG data or streams. You may only add sources you are legally authorized to access. See [docs/LEGAL.md](docs/LEGAL.md).

## Features

- **Sources:** Plex, Jellyfin, Xtream Codes (live, VOD, series), M3U playlists, XMLTV guides.
- **Unified home:** Continue Watching, On Now, Recently Added, Watchlist, Favorites, plus per-source rows.
- **Live TV:** channel list with now/next, EPG grid, catch-up where the source supports it, number-key zapping, last-channel toggle.
- **Search and metadata:** global search, TMDB artwork and details, "Available on" with JustWatch attribution.
- **Playback:** in-app playback (ExoPlayer on TV, hls.js and mpegts.js on web) with resume positions, audio, subtitle and aspect selection, sleep timer, picture-in-picture on web.
- **Hand-off:** Netflix, Hulu, Disney+ and similar apps are launched through deep links ("Open in app"), never scraped or replayed.
- **Profiles:** favorites, progress, theme, language filters and parental PIN are per profile.
- **Hardware transcoding:** optional VA-API (`/dev/dri`) for remux and transcode through the built-in stream proxy.

## Screenshots

<!-- Replace with screenshots once the UI is stable. -->
_Screenshots coming soon._ 

## Install

The image is published at `ghcr.io/walkeryan/streamhub:latest`. Web UI: `http://<server-ip>:8787`.

### Unraid (Community Applications)

Once the template is listed in Community Applications, search for **StreamHub** under Apps. Until then, add it from the template file:

1. Docker tab, **Add Container**, template dropdown, then add a template from URL pointing to `deploy/unraid-template.xml` in this repository.
2. Set **Secret Key** (required), and optionally TMDB API key and admin password.
3. Keep the Config path (default `/mnt/user/appdata/streamhub`). Back this up.
4. Optional: set the **VA-API Device** to `/dev/dri` for hardware transcoding.

### Manual `docker run`

```bash
docker run -d --name streamhub \
  -p 8787:8787 \
  -v /mnt/user/appdata/streamhub:/config \
  --tmpfs /transcode \
  -e PUID=99 -e PGID=100 -e TZ=America/New_York \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  --restart unless-stopped \
  ghcr.io/walkeryan/streamhub:latest
```

For this host, the web UI is at **http://192.168.1.101:8787**.

Add `--device /dev/dri:/dev/dri` for hardware transcoding. Drop `-e PUID`/`-e PGID` if you do not need custom ownership. The defaults are 1000:1000.

### docker-compose

See [deploy/docker-compose.yml](deploy/docker-compose.yml):

```bash
cd deploy
echo "SECRET_KEY=$(openssl rand -hex 32)" > .env
docker compose up -d
```

### Build from source

```bash
docker build -f deploy/Dockerfile -t streamhub .
```

## Reverse proxy (HTTPS)

StreamHub works behind SWAG, Nginx Proxy Manager or Traefik. Three things matter:

1. **WebSocket upgrades** should be forwarded (the config below does this), so any real-time features keep working.
2. **`X-Forwarded-Proto`** must be set, so the server knows the client connected over HTTPS.
3. **Trust the proxy.** The server only honours `X-Forwarded-*` headers from addresses listed in `FORWARDED_ALLOW_IPS` (default `127.0.0.1`). Set it to the proxy's address or Docker network, for example `172.18.0.0/16`.

**SWAG / plain nginx**

```nginx
location / {
    proxy_pass http://streamhub:8787;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;   # live streams
}
```

**Nginx Proxy Manager:** enable **Websockets Support**, and set the advanced option `proxy_buffering off;` if you proxy live TV.

**Traefik (labels):**

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.streamhub.rule=Host(`streamhub.example.com`)
  - traefik.http.routers.streamhub.entrypoints=websecure
  - traefik.http.routers.streamhub.tls.certresolver=letsencrypt
  - traefik.http.services.streamhub.loadbalancer.server.port=8787
```

Traefik forwards WebSockets and sets `X-Forwarded-Proto` by default. Set `FORWARDED_ALLOW_IPS` to Traefik's network.

## Android TV (sideload)

The TV app is installed with `adb`. Build it or download the debug APK from the CI artifact `streamhub-tv-debug-apk`.

1. On the TV: Settings, Device Preferences, About, click **Build** seven times to enable Developer options. Then Developer options, enable **ADB debugging** and **Network debugging** (or **Wireless debugging**).
2. Find the TV's IP address under Network.
3. From a computer on the same network:

   ```bash
   adb connect <tv-ip>:5555
   adb install -r path/to/streamhub-tv-debug.apk
   ```

   Approve the RSA prompt on the TV.
4. Launch StreamHub. Pair it with the server (below).

## First run

1. Open `http://<server>:8787`. If you did not set `ADMIN_PASSWORD`, create the admin password now.
2. Accept the legal notice. It is shown before the first IPTV or M3U source can be added.
3. Add sources: Plex (PIN sign-in), Jellyfin (Quick Connect or password), Xtream, or M3U with XMLTV.
4. Optional: add a TMDB API key under Settings for artwork and metadata.
5. Pair the TV: open the TV app, note the pairing code it shows, and approve that code from the web UI (device-code pairing).

## Legal

StreamHub does not supply content. You are responsible for the sources you configure. Full notice, attribution requirements (TMDB, JustWatch) and trademark statement: [docs/LEGAL.md](docs/LEGAL.md). Deep-link behaviour: [docs/DEEPLINKS.md](docs/DEEPLINKS.md).

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Licence

MIT. See [LICENSE](LICENSE).
