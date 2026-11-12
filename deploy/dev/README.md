# Dev instance

Runs a branch beside the live server without building an image: the published image
supplies Python, ffmpeg and dependencies, and the checkout is mounted over `/app`.

```bash
cd web-app && npm ci && npm run build && cd ..
cp deploy/dev/.env.example /somewhere/outside/the/repo/dev.env   # then edit
docker compose --env-file /somewhere/outside/the/repo/dev.env -f deploy/dev/docker-compose.dev.yml up -d
```

- Server code reloads on save. Rebuild `web-app/dist` after UI changes.
- A new Python dependency needs `docker exec streamhub-dev pip install <pkg>` until the
  next published image includes it.
- The dev instance has its own `/config`; it never touches the live database.
