'use client';

import { useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

type Tri = 'yes' | 'no' | 'unknown';
interface Asset {
  id: string;
  label: string;
  institution_name: string | null;
  jurisdiction: string;
  status: string;
}
interface Citation {
  ref: string;
  title: string;
  version: string;
  section: string;
  heading: string;
  source_url: string;
  effective_from: string;
  fictional: boolean;
}
interface Item {
  id: string;
  requirement: string;
  status: 'completed' | 'pending' | 'unknown';
  note: string;
}
interface PathView {
  key: string;
  title: string;
  section: string;
  condition: string | null;
  note: string | null;
  citation: Citation;
  items: Item[];
}
interface Result {
  status: string;
  case_id: string | null;
  referral: string | null;
  notice: string;
  unknowns: string[];
  paths: PathView[];
  policy: (Partial<Citation> & { ref: string }) | null;
  explanation: { summary: string; next_steps: { action: string; source_refs: string[] }[]; label: string; model_version: string } | null;
}

const STATUS_STYLE: Record<Item['status'], string> = {
  completed: 'bg-trust-100 text-trust-800',
  pending: 'bg-saffron-100 text-saffron-800',
  unknown: 'bg-stone-200 text-stone-700',
};

function TriSelect({ label, value, onChange }: { label: string; value: Tri; onChange: (v: Tri) => void }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-stone-600">{label}</span>
      <select className="input-field h-10" value={value} onChange={(e) => onChange(e.target.value as Tri)}>
        <option value="unknown">Not sure</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </label>
  );
}

export default function ClaimsPreview() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [assetId, setAssetId] = useState('');
  const [survivorship, setSurvivorship] = useState<Tri>('unknown');
  const [dispute, setDispute] = useState<Tri>('unknown');
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch('/api/v1/assets')
      .then((r) => r.json())
      .then((b) => {
        const active = (b.assets ?? []).filter((a: Asset) => a.status === 'active');
        setAssets(active);
        if (active[0]) setAssetId(active[0].id);
      });
  }, []);

  const preview = async () => {
    setBusy(true);
    const res = await fetch('/api/v1/claims', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_id: assetId, survivorship, dispute }),
    });
    setBusy(false);
    if (res.ok) setResult(await res.json());
  };

  return (
    <div className="mt-6">
      <section className="card grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm sm:col-span-2">
          <span className="text-stone-600">Asset</span>
          <select className="input-field h-10" value={assetId} onChange={(e) => setAssetId(e.target.value)}>
            {assets.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label} · {a.institution_name ?? 'no institution'} · {a.jurisdiction}
              </option>
            ))}
          </select>
        </label>
        <TriSelect label="Joint account with an “either or survivor” mandate?" value={survivorship} onChange={setSurvivorship} />
        <TriSelect label="Does any legal heir dispute the claim?" value={dispute} onChange={setDispute} />
        <div className="sm:col-span-2">
          <button className="btn btn-primary" disabled={!assetId || busy} onClick={preview}>
            {busy ? 'Preparing…' : 'Preview checklist'}
          </button>
        </div>
      </section>

      {result && (
        <section className="mt-6 space-y-4" aria-live="polite">
          {result.referral && (
            <p className={cn('rounded-xl px-4 py-3 text-sm', result.status === 'ready' ? 'bg-stone-100 text-stone-700' : 'border border-saffron-300 bg-saffron-50 text-saffron-900')}>
              {result.referral}
            </p>
          )}
          {result.policy?.title && (
            <p className="text-sm text-stone-600">
              Based on <span className="font-medium">{result.policy.title}</span>, version {result.policy.version}, effective{' '}
              {result.policy.effective_from}
              {result.policy.fictional ? ' (fictional)' : ''}.
            </p>
          )}
          {result.paths.map((p) => (
            <article key={p.key} className="card">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-lg font-semibold">{p.title}</h2>
                <a className="text-sm text-trust-700 underline" href={p.citation.source_url} target="_blank" rel="noreferrer">
                  {p.citation.ref}
                  {p.citation.fictional ? ' (fictional)' : ''}
                </a>
              </div>
              {p.condition && <p className="mt-1 text-sm text-stone-600">Applies only if {p.condition}.</p>}
              {p.note && <p className="mt-1 text-sm text-stone-600">{p.note}</p>}
              <ul className="mt-3 divide-y divide-stone-200">
                {p.items.map((i) => (
                  <li key={i.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2 text-sm">
                    <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', STATUS_STYLE[i.status])}>{i.status}</span>
                    <span className="flex-1">{i.requirement}</span>
                    <span className="w-full text-xs text-stone-500 sm:w-auto">{i.note}</span>
                  </li>
                ))}
              </ul>
            </article>
          ))}
          {result.unknowns.length > 0 && (
            <div className="card text-sm">
              <h2 className="font-semibold">Still unknown</h2>
              <ul className="mt-2 list-disc pl-5 text-stone-600">
                {result.unknowns.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
            </div>
          )}
          {result.explanation && (
            <aside className="rounded-xl border border-stone-200 bg-white p-4 text-sm">
              <p className="text-xs font-medium uppercase tracking-wide text-stone-500">{result.explanation.label}</p>
              <p className="mt-2">{result.explanation.summary}</p>
              <ol className="mt-2 list-decimal space-y-1 pl-5">
                {result.explanation.next_steps.map((s, i) => (
                  <li key={i}>
                    {s.action} <span className="text-xs text-stone-500">[{s.source_refs.join(', ')}]</span>
                  </li>
                ))}
              </ol>
              <p className="mt-2 text-xs text-stone-500">Steps written by {result.explanation.model_version}; may contain mistakes.</p>
            </aside>
          )}
          <p className="text-xs text-stone-500">{result.notice}</p>
        </section>
      )}
    </div>
  );
}
