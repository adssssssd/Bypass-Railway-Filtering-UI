/**
 * Path-based CF Worker bridge — same engine as ultron-bridge.
 *
 * The worker reads the backend destination from the URL path itself:
 *
 *   https://WORKER.workers.dev/BACKEND.DOMAIN:443/WS_PATH
 *
 * Examples:
 *   /hermes-railway-template-production-999d.up.railway.app:443/vpn-9x7k2
 *   /panelantirail-production.up.railway.app:443/ws/e5753f19-...
 *
 * One worker can serve ANY number of backends — no env bindings needed.
 */
export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const path = url.pathname;

      // root = health check
      if (path === '/' || path === '') {
        return new Response('OK', { status: 200 });
      }

      // path must look like /HOST:PORT/WS_PATH
      const m = path.match(/^\/([^/]+?)(?::(\d+))?\/(.*)$/);
      if (!m) {
        return new Response('bad path: ' + path, { status: 400 });
      }

      const host = m[1];
      const port = m[2] || '443';
      const wsPath = '/' + m[3];

      // forward with the original method/headers/body
      const upstream = 'https://' + host + ':' + port + wsPath +
        (url.search || '');
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
};