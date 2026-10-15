/** Hosted-demo passcode gate. The cookie holds a hash of the passcode, never the passcode. */
export const GATE_COOKIE = 'reclaim_demo_gate';

export async function gateValue(passcode: string): Promise<string> {
  const data = new TextEncoder().encode(`reclaim-demo-gate:${passcode}`);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}
