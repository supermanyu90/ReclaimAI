/**
 * Server-side API client. The browser never calls the API directly in the
 * scaffold; pages fetch on the server so the API origin stays configurable.
 */

import { backendFetch } from './backend';

export type CapabilityState = 'live' | 'fixture' | 'unavailable';

export interface Capability {
  name: string;
  state: CapabilityState;
  adapter: string;
  detail: string;
}

export interface Readiness {
  status: 'ready' | 'not_ready';
  app_mode: string;
  database: { ok: boolean; revision: string | null; detail: string };
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await backendFetch(path);
    // /readyz answers 503 with a body when the database is down; keep that body.
    if (!res.ok && res.status !== 503) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export const getReadiness = () => getJson<Readiness>('/readyz');
export const getCapabilities = () =>
  getJson<{ app_mode: string; capabilities: Capability[]; optional_features?: { recovery: boolean } }>('/v1/system/capabilities');
