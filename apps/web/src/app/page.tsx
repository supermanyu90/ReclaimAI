import { getCapabilities, getReadiness, type CapabilityState } from '@/lib/api';
import { cn } from '@/lib/utils';

export const dynamic = 'force-dynamic';

const STATE_STYLE: Record<CapabilityState, string> = {
  live: 'bg-trust-100 text-trust-800',
  fixture: 'bg-saffron-100 text-saffron-800',
  unavailable: 'bg-stone-200 text-stone-700',
};

export default async function Home() {
  const [readiness, caps] = await Promise.all([getReadiness(), getCapabilities()]);

  return (
    <main className="mx-auto max-w-3xl px-4 py-12 sm:px-6">
      <p className="text-sm font-medium uppercase tracking-wide text-saffron-600">
        Prepare → Handover → Recover
      </p>
      <h1 className="mt-2 text-4xl font-semibold text-stone-900">ReclaimAI</h1>
      <p className="mt-3 text-lg text-stone-600">
        One maintained record, controlled family access, a clearer path to claims.
      </p>

      <div className="mt-6 flex flex-wrap gap-3">
        <a className="btn btn-primary" href="/chat">Open chat</a>
        <a className="btn btn-secondary" href="/signin">Switch person</a>
      </div>

      {readiness?.app_mode === 'local' && (
        <p className="mt-6 rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-3 text-sm text-saffron-900">
          Local mode. All data is synthetic and every simulated step is labelled. No
          live bank, insurer or Google Cloud service is connected.
        </p>
      )}

      <section className="card mt-8">
        <h2 className="text-lg font-semibold">System status</h2>
        {readiness ? (
          <dl className="mt-3 grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
            <dt className="text-stone-500">API</dt>
            <dd>{readiness.status}</dd>
            <dt className="text-stone-500">Mode</dt>
            <dd>{readiness.app_mode}</dd>
            <dt className="text-stone-500">Database</dt>
            <dd>
              {readiness.database.ok
                ? `migrated (${readiness.database.revision ?? 'no revision'})`
                : readiness.database.detail}
            </dd>
          </dl>
        ) : (
          <p className="mt-3 text-sm text-stone-600">
            API unreachable. Start it with <code>make dev</code>.
          </p>
        )}
      </section>

      {caps && (
        <section className="card mt-6">
          <h2 className="text-lg font-semibold">Capabilities</h2>
          <p className="mt-1 text-sm text-stone-500">
            Reported by each provider adapter. Nothing here is assumed.
          </p>
          <ul className="mt-4 divide-y divide-stone-200">
            {caps.capabilities.map((c) => (
              <li key={c.name} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-3">
                <span className="w-32 font-medium">{c.name}</span>
                <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', STATE_STYLE[c.state])}>
                  {c.state}
                </span>
                <span className="min-w-0 flex-1 text-sm text-stone-600">{c.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
