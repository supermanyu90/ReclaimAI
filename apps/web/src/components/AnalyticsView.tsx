'use client';

import { useEffect, useState } from 'react';
import CategoryReview from './CategoryReview';

interface Flow {
  month: string;
  inflow: string;
  outflow: string;
  transactions: number;
}
interface Flag {
  kind: 'recurring_gap' | 'unusual_amount';
  account: string;
  category: string;
  month?: string;
  date?: string;
  amount?: string;
  rule: string;
  threshold: string;
  sample_size: number;
  explanation: string;
  data_window: { start: string; end: string };
}
interface Summary {
  status: 'ok' | 'insufficient_data' | 'unavailable';
  window: { start: string; end: string };
  sample_size: number;
  flows_by_currency: Record<string, Flow[]>;
  duplicates: { deliveries: number; distinct_events: number; duplicate_rate: number | null; note: string };
  flags: Flag[];
  stale_records: { assets: number; stale: number; rule: string };
  handover_turnaround: { status: string; sample_size: number; median_hours?: number; note?: string };
  pipeline_errors: { topic: string; reason: string; errors: number }[];
  notice: string;
  detail?: string;
}

export default function AnalyticsView() {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/v1/analytics')
      .then(async (r) => (r.ok ? setData(await r.json()) : setError((await r.json()).detail ?? `Request failed (${r.status})`)))
      .catch(() => setError('Network problem.'));
  }, []);

  if (error) return <p className="card mt-6 text-sm">{error}</p>;
  if (!data) return <p className="mt-6 text-sm text-stone-500">Loading…</p>;

  return (
    <div className="mt-4 space-y-6">
      <p className="rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-2 text-sm text-saffron-900">{data.notice}</p>
      {data.status !== 'ok' ? (
        <p className="card text-sm">
          {data.status === 'insufficient_data'
            ? `Not enough data: ${data.sample_size} transactions between ${data.window.start} and ${data.window.end}.`
            : `Analytics unavailable: ${data.detail}`}
        </p>
      ) : (
        <>
          <section>
            <h2 className="text-lg font-semibold">Review flags</h2>
            {data.flags.length === 0 && <p className="mt-2 text-sm text-stone-500">Nothing flagged in this window.</p>}
            <ul className="mt-2 space-y-3">
              {data.flags.map((f, i) => (
                <li key={i} className="card text-sm">
                  <p className="font-medium">
                    {f.kind === 'recurring_gap' ? `Missing ${f.category} in ${f.month}` : `Unusual ${f.amount} on ${f.date}`} ·{' '}
                    {f.account}
                  </p>
                  <p className="mt-1 text-stone-700">{f.explanation}</p>
                  <p className="mt-2 text-xs text-stone-500">
                    Rule: {f.rule}. Threshold: {f.threshold}. Sample: {f.sample_size}. Window {f.data_window.start} to {f.data_window.end}.
                  </p>
                </li>
              ))}
            </ul>
          </section>

          {Object.entries(data.flows_by_currency).map(([cur, rows]) => (
            <section key={cur} className="card overflow-x-auto">
              <h2 className="text-lg font-semibold">{cur} flows by month</h2>
              <table className="mt-2 w-full min-w-[22rem] text-sm">
                <thead className="text-left text-stone-500">
                  <tr>
                    <th className="py-1 font-normal">Month</th>
                    <th className="py-1 text-right font-normal">In</th>
                    <th className="py-1 text-right font-normal">Out</th>
                    <th className="py-1 text-right font-normal">Count</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100">
                  {rows.map((r) => (
                    <tr key={r.month}>
                      <td className="py-1">{r.month}</td>
                      <td className="py-1 text-right tabular-nums">{r.inflow}</td>
                      <td className="py-1 text-right tabular-nums">{r.outflow}</td>
                      <td className="py-1 text-right tabular-nums">{r.transactions}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          ))}
        </>
      )}

      <CategoryReview />

      <section className="card grid gap-2 text-sm sm:grid-cols-2">
        <p>
          <span className="text-stone-500">Duplicate deliveries:</span> {data.duplicates.deliveries - data.duplicates.distinct_events} of{' '}
          {data.duplicates.deliveries} ({data.duplicates.note})
        </p>
        <p>
          <span className="text-stone-500">Stale records:</span> {data.stale_records.stale} of {data.stale_records.assets} ({data.stale_records.rule})
        </p>
        <p>
          <span className="text-stone-500">Handover turnaround:</span>{' '}
          {data.handover_turnaround.status === 'ok' ? `median ${data.handover_turnaround.median_hours} h` : data.handover_turnaround.note}
        </p>
        <p>
          <span className="text-stone-500">Pipeline errors:</span>{' '}
          {data.pipeline_errors.length ? data.pipeline_errors.map((e) => `${e.errors} × ${e.reason}`).join('; ') : 'none'}
        </p>
      </section>
    </div>
  );
}
