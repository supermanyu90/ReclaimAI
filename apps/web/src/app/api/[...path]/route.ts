/**
 * Same-origin proxy to the API. Adds the local dev identity from an httpOnly cookie,
 * so the browser never holds a bearer token. Only /v1/ paths are forwarded.
 */
import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';
import { backendFetch } from '@/lib/backend';

const DEV_USER_COOKIE = 'reclaim_dev_user';
const UUID = /^[0-9a-f-]{36}$/i;

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path[0] !== 'v1') return NextResponse.json({ detail: 'not found' }, { status: 404 });

  // Pass the body through untouched: JSON for most calls, multipart for uploads.
  const headers: Record<string, string> = { 'Content-Type': req.headers.get('content-type') ?? 'application/json' };
  const user = (await cookies()).get(DEV_USER_COOKIE)?.value;
  if (user && UUID.test(user)) headers.Authorization = `Bearer dev:${user}`;
  const key = req.headers.get('idempotency-key');
  if (key) headers['Idempotency-Key'] = key;

  const res = await backendFetch(`/${path.map(encodeURIComponent).join('/')}${req.nextUrl.search}`, {
    method: req.method,
    headers,
    body: req.method === 'GET' ? undefined : await req.arrayBuffer(),
  });
  const passthrough: Record<string, string> = { 'Content-Type': res.headers.get('content-type') ?? 'application/json' };
  for (const h of ['content-disposition', 'cache-control', 'x-content-type-options']) {
    const v = res.headers.get(h);
    if (v) passthrough[h] = v;
  }
  return new NextResponse(await res.arrayBuffer(), { status: res.status, headers: passthrough });
}

export { forward as GET, forward as POST, forward as PATCH };
