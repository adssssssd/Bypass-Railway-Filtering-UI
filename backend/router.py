#!/usr/bin/env python3
"""Router: Railway public :8080 -> xray VLESS+WS on 127.0.0.1:3128.

- /vpn-* and /ws/*  -> proxied to xray (VLESS+WS tunnel)
- /api/status       -> JSON: xray/router health + credentials (UUID, WS path)
- /api/build-worker -> POST: deploy a path-based CF Worker with a CF API token
- /panel            -> management panel (index.html)
- everything else   -> status page
"""
import asyncio
import json
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
XRAY_PORT = int(os.environ.get("XRAY_PORT", "3128"))
UUID = os.environ.get("UUID", "") or str(_uuid.uuid4())
WS_PATH = os.environ.get("WS_PATH", "") or "/vpn-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
BACKEND = os.environ.get("BACKEND_DOMAIN", os.environ.get("RAILWAY_PUBLIC_DOMAIN", ""))

WS_PATTERN = re.compile(r"^/(?:vpn-[^/]+(?:/.*)?|ws/[^/]+(?:/.*)?)$")

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static", "index.html")

CF_API = "https://api.cloudflare.com/client/v4"
COMPAT_DATE = "2024-01-01"

# Path-based bridge worker (same engine as worker/worker.js) — reads the
# backend destination from the URL path: /BACKEND.DOMAIN:443/WS_PATH
WORKER_JS = r'''export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const path = url.pathname;
      if (path === '/' || path === '')
        return new Response('OK', { status: 200 });
      const m = path.match(/^\/([^/]+?)(?::(\d+))?\/(.*)$/);
      if (!m)
        return new Response('bad path: ' + path, { status: 400 });
      const host = m[1];
      const port = m[2] || '443';
      const wsPath = '/' + m[3];
      const upstream = 'https://' + host + ':' + port + wsPath + (url.search || '');
      const resp = await fetch(upstream, {
        method: request.method,
        headers: request.headers,
        body: request.body,
        cf: { resolveOverride: host },
      });
      return new Response(resp.body, {
        status: resp.status,
        statusText: resp.statusText,
        headers: resp.headers,
      });
    } catch (e) {
      return new Response('bridge error: ' + e.message, { status: 502 });
    }
  },
};'''

STATUS_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Bypass-Railway-Filtering</title>
<style>body{font-family:system-ui;background:#0a0f26;color:#e6ebff;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#121a3d;padding:40px 60px;border-radius:16px;text-align:center;
border:1px solid rgba(124,108,255,.3)} h1{font-size:28px;margin:0 0 8px}
.ok{color:#4ee6c8} code{background:#0a0f26;padding:4px 10px;border-radius:6px}</style>
</head><body><div class="card"><h1>⚡ Bypass-Railway-Filtering</h1>
<p>VLESS+WS tunnel is <span class="ok">UP</span></p>
<p>WS path: <code>{path}</code></p>
<p><a href="/panel" style="color:#7c6cff">Open panel →</a></p></div></body></html>"""


# ---------------------------------------------------------------------------
# Cloudflare worker builder (used by /api/build-worker)
# ---------------------------------------------------------------------------
def _cf_request(method, path, token, data=None, headers=None):
    url = CF_API + path
    h = {"Authorization": "Bearer " + token, "User-Agent": "bypass-railway-ui/1.0"}
    if headers:
        h.update(headers)
    body = None
    if isinstance(data, bytes):
        body = data
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    j = json.loads(raw)
    if not j.get("success"):
        raise RuntimeError("CF error on %s: %s" % (path, j.get("errors") or []))
    return j.get("result")


def build_worker_with_token(token, backend, ws_path=None, wname=None, uuid=None):
    """Deploy a path-based worker and return the final vless link."""
    token = token.strip()
    if not token:
        raise RuntimeError("token is empty")
    backend = (backend or "").strip().lower()
    if not backend or "://" in backend:
        raise RuntimeError("backend domain is invalid")
    backend = backend.split("/")[0]

    # credentials (auto-generate what's missing)
    if not ws_path or not ws_path.strip():
        ws_path = "/vpn-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    if not ws_path.startswith("/"):
        ws_path = "/" + ws_path
    uuid = (uuid or "").strip() or str(_uuid.uuid4())
    if not re.match(r"^[0-9a-fA-F-]{8,64}$", uuid):
        raise RuntimeError("invalid UUID")
    if not wname or not wname.strip():
        wname = "svc-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", wname):
        raise RuntimeError("invalid worker name")

    # account
    accs = _cf_request("GET", "/accounts", token)
    if not accs:
        raise RuntimeError("no accounts found for this token")
    acc = accs[0]
    acc_id = acc["id"]
    sub_res = _cf_request("GET", "/accounts/%s/workers/subdomain" % acc_id, token)
    sub = (sub_res or {}).get("subdomain", "")
    if not sub:
        raise RuntimeError("workers.dev subdomain not enabled on this account")

    # upload worker (multipart)
    boundary = "----bypass" + os.urandom(6).hex()
    meta = json.dumps({
        "main_module": "worker.js",
        "compatibility_date": COMPAT_DATE,
        "bindings": [],
    })
    parts = [
        "--" + boundary,
        'Content-Disposition: form-data; name="metadata"; filename="metadata.json"',
        "Content-Type: application/json", "", meta,
        "--" + boundary,
        'Content-Disposition: form-data; name="worker.js"; filename="worker.js"',
        "Content-Type: application/javascript+module", "", WORKER_JS,
        "--" + boundary + "--", "",
    ]
    body = "\r\n".join(parts).encode("utf-8")
    headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
    _cf_request("PUT", "/accounts/%s/workers/scripts/%s" % (acc_id, wname),
                token, data=body, headers=headers)

    # enable subdomain
    try:
        _cf_request("POST", "/accounts/%s/workers/scripts/%s/subdomain" % (acc_id, wname),
                    token, data={"enabled": True})
    except Exception:
        pass

    time.sleep(5)

    # final link
    worker = "%s.%s.workers.dev" % (wname, sub)
    enc = urllib.parse.quote("/%s:443%s" % (backend, ws_path), safe="")
    link = ("vless://%s@%s:443?path=%s"
            "&security=tls&encryption=none&insecure=0&host=%s"
            "&fp=chrome&type=ws&allowInsecure=0&sni=%s#Bypass-Railway"
            % (uuid, worker, enc, worker, worker))
    return {"worker": worker, "uuid": uuid, "ws_path": ws_path,
            "subdomain": sub, "link": link}


async def probe(host, port, timeout=2.0):
    try:
        r, w = await asyncio.open_connection(host, port)
        w.close()
        return True
    except Exception:
        return False


async def proxy(reader, writer):
    try:
        xr_reader, xr_writer = await asyncio.open_connection("127.0.0.1", XRAY_PORT)
    except OSError:
        try:
            writer.close()
        except Exception:
            pass
        return
    async def pipe(src, dst):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass
    await asyncio.gather(pipe(reader, xr_writer), pipe(xr_reader, writer))


async def handle(reader, writer):
    try:
        req = await reader.read(65536)
        if not req:
            writer.close()
            return
        line = req.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = line.split(" ")
        if len(parts) < 2:
            writer.close()
            return
        method, target = parts[0], parts[1]
        path = target.split("?")[0]

        # --- WS tunnel paths -> xray ---
        if WS_PATTERN.match(path) and method in ("GET", "POST", "PUT"):
            await proxy(reader, writer)
            return

        # --- JSON status API ---
        if path == "/api/status":
            xray_ok = await probe("127.0.0.1", XRAY_PORT)
            body = json.dumps({
                "xray": xray_ok,
                "router": True,
                "uuid": UUID,
                "ws_path": WS_PATH,
                "backend": BACKEND,
                "port": PORT,
            }).encode()
            resp = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                    + str(len(body)).encode() + b"\r\n\r\n" + body)
            writer.write(resp)
            await writer.drain()
            writer.close()
            return

        # --- build worker ---
        if path == "/api/build-worker" and method == "POST":
            # read body
            body_bytes = req.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in req else b""
            try:
                payload = json.loads(body_bytes.decode("utf-8", "replace") or "{}")
            except Exception:
                payload = {}
            def do_build():
                return build_worker_with_token(
                    payload.get("token", ""),
                    payload.get("backend", ""),
                    payload.get("ws_path", ""),
                    payload.get("wname", ""),
                    payload.get("uuid", "") or UUID)
            try:
                result = await asyncio.to_thread(do_build)
                out = json.dumps(result).encode()
                resp = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                        + str(len(out)).encode() + b"\r\n\r\n" + out)
            except Exception as e:
                out = json.dumps({"error": str(e)}).encode()
                resp = (b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
                        b"Access-Control-Allow-Origin: *\r\nContent-Length: "
                        + str(len(out)).encode() + b"\r\n\r\n" + out)
            writer.write(resp)
            await writer.drain()
            writer.close()
            return

        # --- management panel ---
        if path in ("/panel", "/panel/"):
            try:
                with open(STATIC, "rb") as f:
                    body = f.read()
                resp = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                writer.write(resp)
                await writer.drain()
                writer.close()
                return
            except FileNotFoundError:
                pass  # fall through to status page

        # --- default: simple status page ---
        # NOTE: use .replace() not .format() — the CSS braces ({font-family…})
        # would otherwise raise KeyError. Only one real placeholder ({path}).
        body = STATUS_PAGE.replace("{path}", WS_PATH).encode()
        resp = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body)
        writer.write(resp)
        await writer.drain()
        writer.close()
    except Exception:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    global WS_PATH
    WS_PATH = os.environ.get("WS_PATH", WS_PATH)
    srv = await asyncio.start_server(handle, HOST, PORT)
    print(f"[router] listening on {HOST}:{PORT} -> xray 127.0.0.1:{XRAY_PORT} (ws_path={WS_PATH})")
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)