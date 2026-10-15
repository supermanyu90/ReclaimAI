'use client';

import { useCallback, useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

interface Check {
  check: string;
  result: string;
  detail: string;
}
interface Case {
  id: string;
  state: string;
  owner: string;
  requester: string;
  approvals_current: number;
  approvals_required: number;
  evidence_version: number;
  evidence: { document_id: string; title: string; kind: string; status: string; review_reasons: string[] }[];
  decisions: { reviewer: string; decision: string; evidence_version: number; counts: boolean; at: string }[];
  sharing?: { sections: string[]; assets: string[] };
  checks?: Check[];
  ai_summary?: { case_summary: string; conflicts: string[]; missing_items: string[]; next_review: string; label: string } | null;
}

const ACTIONS: Record<string, { decision: string; label: string; primary?: boolean }[]> = {
  VERIFICATION_PENDING: [
    { decision: 'approve', label: 'Approve', primary: true },
    { decision: 'hold', label: 'Hold' },
    { decision: 'needs_information', label: 'Ask for more information' },
    { decision: 'reject', label: 'Reject' },
  ],
  HOLD: [
    { decision: 'resume', label: 'Resume review' },
    { decision: 'needs_information', label: 'Ask for more information' },
    { decision: 'reject', label: 'Reject' },
  ],
  REQUESTED: [{ decision: 'reject', label: 'Reject' }],
  NEEDS_INFORMATION: [{ decision: 'reject', label: 'Reject' }],
};

function CaseCard({ id, onChange }: { id: string; onChange: () => void }) {
  const [c, setC] = useState<Case | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    const r = await fetch(`/api/v1/handovers/${id}`);
    if (r.ok) setC(await r.json());
  }, [id]);
  useEffect(() => {
    load();
  }, [load]);
  const decide = async (decision: string) => {
    setError(null);
    const r = await fetch(`/api/v1/handovers/${id}/decisions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    if (!r.ok) setError((await r.json().catch(() => ({})))?.detail?.message ?? `Request failed (${r.status})`);
    await load();
    onChange();
  };
  if (!c) return null;
  return (
    <article className="card space-y-3 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">
          {c.requester} requests {c.owner}&apos;s records
        </h2>
        <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs">
          {c.state.replace(/_/g, ' ').toLowerCase()} · {c.approvals_current}/{c.approvals_required} current approvals
        </span>
      </div>
      <div>
        <h3 className="font-medium">Evidence (version {c.evidence_version})</h3>
        <ul className="mt-1 list-disc pl-5">
          {c.evidence.map((e) => (
            <li key={e.document_id}>
              {e.title} · {e.kind.replace(/_/g, ' ')} · {e.status.replace(/_/g, ' ')}
              {e.review_reasons.length ? <span className="text-stone-500"> ({e.review_reasons.join('; ')})</span> : null}
            </li>
          ))}
        </ul>
      </div>
      {c.checks && c.checks.length > 0 && (
        <ul className="space-y-1">
          {c.checks.map((k, i) => (
            <li key={i} className={cn('rounded px-2 py-1', k.result === 'match' ? 'bg-trust-50 text-trust-900' : 'bg-red-50 text-red-900')}>
              {k.detail}
            </li>
          ))}
        </ul>
      )}
      {c.ai_summary && (
        <aside className="rounded-lg border border-stone-200 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-stone-500">{c.ai_summary.label}</p>
          <p className="mt-1">{c.ai_summary.case_summary}</p>
          {c.ai_summary.conflicts.length > 0 && <p className="mt-1 text-red-800">Discrepancies: {c.ai_summary.conflicts.join('; ')}</p>}
          {c.ai_summary.missing_items.length > 0 && <p className="mt-1 text-saffron-800">Missing: {c.ai_summary.missing_items.join('; ')}</p>}
        </aside>
      )}
      {c.sharing && (
        <p className="text-stone-600">
          Owner&apos;s sharing instructions after release: {c.sharing.assets.join(', ')} ({c.sharing.sections.join(', ').replace(/_/g, ' ')}).
        </p>
      )}
      {c.decisions.length > 0 && (
        <ul className="text-xs text-stone-500">
          {c.decisions.map((d, i) => (
            <li key={i}>
              {d.reviewer}: {d.decision.replace(/_/g, ' ')} on evidence v{d.evidence_version}
              {d.decision === 'approve' ? (d.counts ? ' (counts)' : ' (no longer counts: evidence or grant changed)') : ''}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        {(ACTIONS[c.state] ?? []).map((a) => (
          <button key={a.decision} className={cn('btn text-sm', a.primary ? 'btn-primary' : 'btn-secondary')} onClick={() => decide(a.decision)}>
            {a.label}
          </button>
        ))}
      </div>
      {error && <p className="text-red-700">{error}</p>}
    </article>
  );
}

export default function ReviewerView() {
  const [cases, setCases] = useState<{ id: string }[] | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    const r = await fetch('/api/v1/reviews');
    if (!r.ok) return setError('This page is for reviewers.');
    const body = await r.json();
    setCases(body.cases);
    setNote(body.simulated_verification);
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  if (error) return <p className="card mt-6 text-sm">{error}</p>;
  if (!cases) return <p className="mt-6 text-sm text-stone-500">Loading…</p>;
  return (
    <div className="mt-6 space-y-4">
      <p className="rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-2 text-sm text-saffron-900">
        Synthetic demo. {note} Checks and the AI summary describe documents; only reviewers decide.
      </p>
      {cases.length === 0 && <p className="text-sm text-stone-500">No open cases.</p>}
      {cases.map((c) => (
        <CaseCard key={c.id} id={c.id} onChange={load} />
      ))}
    </div>
  );
}
