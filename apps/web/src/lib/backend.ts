/**
 * Server-side calls to the API. On the hosted demo the API is private: Cloud Run admits only
 * this web service's identity, sent as a Google ID token in X-Serverless-Authorization, so the
 * Authorization header stays free for the app's own (synthetic-person) credential.
 * Locally API_AUDIENCE is unset and nothing is added.
 */

export const API_URL = process.env.API_URL ?? 'http://localhost:8100';
const AUDIENCE = process.env.API_AUDIENCE;
const METADATA = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity';

let cached: { token: string; expires: number } | null = null;

async function identityToken(): Promise<string | null> {
  if (!AUDIENCE) return null;
  if (cached && cached.expires > Date.now()) return cached.token;
  const res = await fetch(`${METADATA}?audience=${encodeURIComponent(AUDIENCE)}`, { headers: { 'Metadata-Flavor': 'Google' }, cache: 'no-store' });
  if (!res.ok) throw new Error(`metadata identity token: ${res.status}`);
  // Google ID tokens last an hour; refresh well before that.
  cached = { token: await res.text(), expires: Date.now() + 45 * 60 * 1000 };
  return cached.token;
}

export async function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = await identityToken();
  if (token) headers.set('X-Serverless-Authorization', `Bearer ${token}`);
  return fetch(`${API_URL}${path}`, { ...init, headers, cache: 'no-store' });
}
