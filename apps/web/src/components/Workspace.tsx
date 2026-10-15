'use client';

import { useCallback, useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

interface Asset {
  id: string;
  label: string;
  asset_type: string;
  institution_name: string | null;
  jurisdiction: string;
  masked_reference: string | null;
  currency: string | null;
  amounts: Record<string, number>;
  source_date: string | null;
  verification_state: string;
  nomination_status: string;
  document_location: string | null;
  status: string;
}
interface Node {
  id: string;
  kind: 'owner' | 'asset' | 'institution' | 'person' | 'document';
  label: string | null;
}
interface Edge {
  from: string;
  to: string;
  kind: string;
  detail?: string | null;
  field?: string;
}
interface Finding {
  asset: string;
  kind: string;
  label: string;
  detail: string;
}
interface Preview {
  name: string;
  note: string;
  pre_handover?: { sections: string[]; assets: { label: string }[] };
  post_release?: { sections: string[]; assets: { label: string }[]; hidden_assets: string[] };
}
interface Passport {
  id: string;
  version: number;
  status: 'current' | 'stale';
  audience: string;
  recipient_user_id: string | null;
  generated_at: string;
  assets: number;
}
interface Overview {
  assets: Asset[];
  graph: { nodes: Node[]; edges: Edge[] };
  timeline: { at: string; text: string }[];
  review_queue: { kind: string; text: string }[];
  findings: Finding[];
  access_preview: (Preview & { grantee_user_id: string })[];
}

const COLUMNS: Node['kind'][] = ['person', 'owner', 'asset', 'institution', 'document'];
const FINDING_STYLE: Record<string, string> = {
  stale_source: 'bg-saffron-100 text-saffron-800',
  unknown_nomination: 'bg-trust-100 text-trust-800',
  missing_document_location: 'bg-stone-200 text-stone-700',
  no_source_date: 'bg-stone-200 text-stone-700',
};
const fmt = (minor: number, cur: string | null) =>
  cur === 'INR'
    ? `₹${(minor / 100).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
    : `${cur === 'SGD' ? 'S$' : (cur ?? '') + ' '}${(minor / 100).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
const KIND: Record<string, string> = {
  balance_minor: 'balance',
  estimated_value_minor: 'estimate',
  insurance_cover_minor: 'cover',
  sip_installment_minor: 'SIP / month',
  liability_minor: 'loan owed',
};

function Graph({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) {
  const colW = 190;
  const rowH = 46;
  const cols = COLUMNS.map((k) => nodes.filter((n) => n.kind === k));
  const height = Math.max(...cols.map((c) => c.length), 1) * rowH + 30;
  const pos: Record<string, { x: number; y: number }> = {};
  cols.forEach((col, ci) => {
    const offset = (height - col.length * rowH) / 2;
    col.forEach((n, ri) => (pos[n.id] = { x: 10 + ci * colW, y: offset + ri * rowH + rowH / 2 }));
  });
  const width = COLUMNS.length * colW + 10;
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label="Recorded relationships">
        {edges.map((e, i) => {
          const a = pos[e.from];
          const b = pos[e.to];
          if (!a || !b) return null;
          const [l, r] = a.x <= b.x ? [a, b] : [b, a];
          const x1 = l.x + 160;
          const mid = (x1 + r.x) / 2;
          return (
            <g key={i}>
              <path d={`M ${x1} ${l.y} C ${mid} ${l.y}, ${mid} ${r.y}, ${r.x} ${r.y}`} fill="none" stroke="#a8a29e" strokeWidth="1" />
              <title>{`${e.kind.replace(/_/g, ' ')}${e.detail ? `: ${e.detail}` : ''}${e.field ? ` (${e.field})` : ''}`}</title>
            </g>
          );
        })}
        {nodes.map((n) => {
          const p = pos[n.id];
          if (!p) return null;
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y - 15})`}>
              <rect width="160" height="30" rx="8" fill={n.kind === 'owner' ? '#ff6b35' : n.kind === 'asset' ? '#ffffff' : '#f5f5f4'} stroke="#d6d3d1" />
              <text x="80" y="19" textAnchor="middle" fontSize="11" fill={n.kind === 'owner' ? '#ffffff' : '#1c1917'}>
                {(n.label ?? '').length > 24 ? `${(n.label ?? '').slice(0, 23)}…` : n.label}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-xs text-stone-500">Columns: people · you · assets · institutions · supporting documents. Lines are recorded links only; hover for what each records.</p>
    </div>
  );
}

export default function Workspace() {
  const [data, setData] = useState<Overview | null>(null);
  const [passports, setPassports] = useState<Passport[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [w, p] = await Promise.all([fetch('/api/v1/workspace'), fetch('/api/v1/passports')]);
    if (w.ok) setData(await w.json());
    if (p.ok) setPassports((await p.json()).passports);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const generate = async (recipient?: string) => {
    setBusy(true);
    await fetch('/api/v1/passports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(recipient ? { recipient_user_id: recipient } : {}),
    });
    setBusy(false);
    load();
  };

  if (!data) return <p className="mt-6 text-sm text-stone-500">Loading…</p>;
  const findingsFor = (label: string) => data.findings.filter((f) => f.asset === label);

  return (
    <div className="mt-6 space-y-6">
      <section className="card overflow-x-auto">
        <h2 className="text-lg font-semibold">Recorded assets</h2>
        <table className="mt-2 w-full min-w-[40rem] text-sm">
          <thead className="text-left text-stone-500">
            <tr>
              <th className="py-1 font-normal">Asset</th>
              <th className="py-1 font-normal">Amounts (as recorded)</th>
              <th className="py-1 font-normal">As of</th>
              <th className="py-1 font-normal">Basis</th>
              <th className="py-1 font-normal">To check</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100 align-top">
            {data.assets.map((a) => (
              <tr key={a.id} className={cn(a.status !== 'active' && 'text-stone-400')}>
                <td className="py-2">
                  <span className="block font-medium">{a.label}</span>
                  <span className="text-xs text-stone-500">
                    {a.institution_name ?? 'no institution'} · {a.jurisdiction}
                    {a.masked_reference ? ` · ${a.masked_reference}` : ''}
                    {a.status !== 'active' ? ' · closed' : ''}
                  </span>
                </td>
                <td className="py-2">
                  {Object.entries(a.amounts).map(([k, v]) => (
                    <span key={k} className="block tabular-nums">
                      {fmt(v, a.currency)} <span className="text-xs text-stone-500">{KIND[k]}</span>
                    </span>
                  ))}
                </td>
                <td className="py-2">{a.source_date ?? 'unknown'}</td>
                <td className="py-2 text-xs">
                  {a.verification_state.replace(/_/g, ' ')}
                  <span className="block text-stone-500">nomination {a.nomination_status.replace(/_/g, ' ')}</span>
                </td>
                <td className="py-2">
                  {findingsFor(a.label).map((f) => (
                    <span key={f.kind} title={f.detail} className={cn('mb-1 mr-1 inline-block rounded-full px-2 py-0.5 text-xs', FINDING_STYLE[f.kind])}>
                      {f.label}
                    </span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold">How it connects</h2>
        <Graph nodes={data.graph.nodes} edges={data.graph.edges} />
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <section className="card text-sm">
          <h2 className="text-lg font-semibold">Needs your attention</h2>
          {data.review_queue.length === 0 ? (
            <p className="mt-2 text-stone-500">Nothing waiting.</p>
          ) : (
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {data.review_queue.map((r, i) => (
                <li key={i}>{r.text}</li>
              ))}
            </ul>
          )}
        </section>
        <section className="card text-sm">
          <h2 className="text-lg font-semibold">What your family can see</h2>
          {data.access_preview.map((p) => (
            <div key={p.name} className="mt-2">
              <p className="font-medium">{p.name}</p>
              <p className="text-stone-600">Now (before any handover): {p.pre_handover?.sections.map((s) => s.replace(/_/g, ' ')).join(', ') || 'nothing'}; no asset details.</p>
              <p className="text-stone-600">
                After a reviewed release: {p.post_release?.assets.map((a) => a.label).join(', ') || 'nothing'}.
                {p.post_release?.hidden_assets.length ? ` Not shared: ${p.post_release.hidden_assets.join(', ')}.` : ''}
              </p>
              <p className="mt-1 text-xs text-stone-500">{p.note}</p>
            </div>
          ))}
        </section>
      </div>

      <section className="card text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Family Asset Passport</h2>
          <div className="flex flex-wrap gap-2">
            <button className="btn btn-primary text-sm" disabled={busy} onClick={() => generate()}>
              Generate my passport
            </button>
            {data.access_preview.map((p) => (
              <button key={p.grantee_user_id} className="btn btn-secondary text-sm" disabled={busy} onClick={() => generate(p.grantee_user_id)}>
                Export for {p.name}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-1 text-xs text-stone-500">
          Built from confirmed records only. Any confirmed change marks earlier versions stale. A downloaded copy cannot be revoked.
        </p>
        <ul className="mt-3 divide-y divide-stone-100">
          {passports.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span>
                {p.audience === 'owner' ? 'Your passport' : `Export for ${p.audience}`} · version {p.version} · {p.assets} assets ·{' '}
                {new Date(p.generated_at).toLocaleString()}
              </span>
              <span className="flex items-center gap-2">
                <span className={cn('rounded-full px-2 py-0.5 text-xs', p.status === 'current' ? 'bg-trust-100 text-trust-800' : 'bg-stone-200 text-stone-600')}>
                  {p.status}
                </span>
                <a className="text-trust-700 underline" href={`/api/v1/passports/${p.id}/download`}>
                  Download
                </a>
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="card text-sm">
        <h2 className="text-lg font-semibold">Timeline</h2>
        <ol className="mt-2 space-y-1">
          {data.timeline.map((t, i) => (
            <li key={i} className="flex gap-3">
              <span className="w-36 shrink-0 text-stone-500">{new Date(t.at).toLocaleDateString()}</span>
              <span>{t.text}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
