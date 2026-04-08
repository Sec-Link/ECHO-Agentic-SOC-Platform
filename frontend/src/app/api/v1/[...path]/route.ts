export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function getBackendOrigins() {
  const primary = process.env.BACKEND_ORIGIN;
  if (primary && primary.trim()) return [primary.trim()];
  return ['http://localhost:8000', 'http://backend:8000'];
}

function shouldDropHeader(key: string) {
  const k = key.toLowerCase();
  return k === 'host' || k === 'connection' || k === 'content-length';
}

async function getParams(ctx: any): Promise<{ path: string[] }> {
  // Next 15 typed helpers sometimes model params as Promise.
  // Route handlers may receive plain objects; this normalizes both.
  const p = await Promise.resolve(ctx?.params);
  return p || { path: [] };
}

async function proxy(request: Request, ctx: any) {
  const { path } = await getParams(ctx);
  const incomingUrl = new URL(request.url);

  const encodedPath = (path || []).map((s) => encodeURIComponent(String(s))).join('/');
  const hasTrailingSlash = incomingUrl.pathname.endsWith('/');
  const targetPath = `/api/v1/${encodedPath}${hasTrailingSlash ? '/' : ''}`;
  const targetOrigins = getBackendOrigins();
  const targetUrl = new URL(targetOrigins[0] + targetPath);
  targetUrl.search = incomingUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!shouldDropHeader(key)) headers.set(key, value);
  });

  // Make server-side proxying simpler/more predictable.
  headers.delete('accept-encoding');

  const method = request.method.toUpperCase();
  const body = method === 'GET' || method === 'HEAD' ? undefined : await request.arrayBuffer();

  async function doFetch(url: URL) {
    return fetch(url, {
      method,
      headers,
      body: body as any,
      redirect: 'manual',
      cache: 'no-store',
    });
  }

  let res: Response;
  for (const origin of targetOrigins) {
    const url = new URL(origin + targetPath);
    url.search = incomingUrl.search;
    try {
      res = await doFetch(url);
      break;
    } catch {
      // try next origin
    }
  }
  if (!res) {
    return new Response('Backend unavailable', { status: 502 });
  }

  // Django/DRF often issues 301 to append trailing slashes.
  // If we pass that redirect to the browser, it can loop with Next's routing.
  // Instead, follow one "append slash" redirect server-side while preserving method/body.
  if ((res.status === 301 || res.status === 308) && res.headers.get('location')) {
    const loc = res.headers.get('location') || '';
    const resolved = new URL(loc, res.url);
    const baseUrl = new URL(res.url);

    const sameOrigin = resolved.origin === baseUrl.origin;
    const sameQuery = resolved.search === incomingUrl.search;
    const isAppendSlash =
      sameOrigin &&
      sameQuery &&
      !baseUrl.pathname.endsWith('/') &&
      resolved.pathname === `${baseUrl.pathname}/`;

    if (isAppendSlash) {
      res = await doFetch(resolved);
    }
  }

  const outHeaders = new Headers(res.headers);
  outHeaders.delete('content-encoding');
  outHeaders.delete('content-length');

  return new Response(res.body, {
    status: res.status,
    headers: outHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
