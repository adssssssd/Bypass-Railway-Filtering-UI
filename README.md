# ⚡ Bypass-Railway-Filtering-UI

Xray VLESS+WS Railway backend + path-based Cloudflare Worker bridge, with a
**web panel** that can also **deploy workers directly with your Cloudflare API token**.

## Architecture

```
Client (v2rayNG/Hiddify)
   │  vless://UUID@WORKER.workers.dev:443?path=/%2FBACKEND:443%2FWS_PATH
   ▼
Cloudflare Worker  (path-based bridge, one worker for ANY backend)
   │  rewrites /BACKEND.DOMAIN:443/WS_PATH  ->  https://BACKEND.DOMAIN:443/WS_PATH
   ▼
Railway app  (this repo)
   │  public :8080  router.py ───► xray 127.0.0.1:3128 (VLESS+WS)
   ▼
Internet
```

## Deploy on Railway

1. Create a Railway project from this repo (or `railway up`).
2. Public port must be **8080**.
3. Optionally set env vars (otherwise random at boot, printed in logs):
   - `UUID` — VLESS client id
   - `WS_PATH` — e.g. `/vpn-xxxxxx`
4. Health check path: `/`

The Dockerfile installs the latest Xray at build time.

## Panel (`/panel`)

- **🤖 Build Cloudflare Worker** — paste your CF API token (`cfut_…`) +
  backend domain; the panel calls `/api/build-worker`, which:
  - detects your CF account + workers.dev subdomain
  - auto-generates UUID + WS path (or use yours)
  - deploys the path-based worker
  - returns the final `vless://` link (copy button)
- **🔑 Credentials / manual link** — generate a client link from
  UUID / WS path / backend / optional worker domain.
- **🩺 Status** — xray/router health + current credentials from `/api/status`.

## Files

```
Dockerfile                # python:3.11-slim + latest Xray
railway.toml              # build/deploy config
backend/entrypoint.sh     # generates UUID/WS_PATH, starts xray + router
backend/router.py         # :8080 router, /api/status, /api/build-worker, /panel
backend/xray/config.template.json
backend/static/index.html # panel UI
worker/worker.js          # path-based bridge worker (same as deployed by the panel)
x4g.py                    # standalone desktop/CLI generator (optional)
```

## Client

Any VLESS client (v2rayNG, Hiddify, Nekoray…). v2rayNG "ping" always fails
for VLESS-over-WS — test by browsing.