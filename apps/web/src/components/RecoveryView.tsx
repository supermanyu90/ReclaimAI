'use client';

import { useCallback, useEffect, useState } from 'react';
import { cn } from '@/lib/utils';

interface Feature {
  key: string;
  label: string;
  registry_value: string | null;
  profile_value: string | null;
  result: 'match' | 'partial' | 'unknown' | 'no_match' | 'conflict';
  points: number;
  max_points: number;
  detail: string;
}
interface Match {
  id: string;
  status: 'candidate' | 'not_mine' | 'claim_opened';
  score: number;
  band: string;
  features: Feature[];
  explanation: { statements: { feature: string; text: string }[]; missing: string[]; label: string; model_version: string | null } | null;
  record: {
    id: string;
    holder_name: string;
    institution: string;
    product: string;
    account_ref: string;
    amount_minor: number;
    currency: string;
    last_activity: string;
    transferred_to_registry: string;
  };
  claims: { id: string; status: string; mine: boolean; checklist: { step: string }[] }[];
}
interface View {
  notice: string;
  score_label: string;
  registry: { name: string; version: string; records: number };
  case_id: string | null;
  profile: {
    legal_name: string;
    name_variants: string[];
    date_of_birth: string | null;
    addresses: { line: string; pincode: string; from?: string; to?: string | null }[];
    opted_in: boolean;
    consent_text: string;
    editable_by_viewer: boolean;
  } | null;
  matches: Match[];
  ruled_out: { record_id: string; holder_name: string; blocked_by: string[] }[];
}

const RESULT_STYLE: Record<Feature['result'], string> = {
  match: 'bg-trust-100 text-trust-900',
  partial: 'bg-saffron-100 text-saffron-900',
  unknown: 'bg-stone-100 text-stone-700',
  no_match: 'bg-stone-200 text-stone-700',
  conflict: 'bg-red-50 text-red-800',
};
const REASON: Record<string, string> = {
  date_of_birth_conflict: 'date of birth differs',
  first_name_conflict: 'first name differs',
  surname_conflict: 'surname differs',
};
const PRODUCT: Record<string, string> = { fixed_deposit: 'Fixed deposit', savings: 'Savings account' };
const inr = (minor: number, cur: string) => (cur === 'INR' ? `₹${(minor / 100).toLocaleString('en-IN')}` : `${cur} ${(minor / 100).toFixed(2)}`);

function MatchCard({ m, scoreLabel, act }: { m: Match; scoreLabel: string; act: (id: string, d: string) => void }) {
  const weak = m.band === 'weak evidence';
  return (
    <article className={cn('card space-y-3', weak && 'opacity-80')}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs text-stone-500">
            Registry record {m.record.id} · {m.record.institution} · {m.record.account_ref}
          </p>
          <h3 className="text-lg font-semibold">
            Possible {PRODUCT[m.record.product]?.toLowerCase() ?? m.record.product}: {inr(m.record.amount_minor, m.record.currency)}
          </h3>
          <p className="text-sm text-stone-600">
            Held as “{m.record.holder_name}”, last active {m.record.last_activity}, moved to the registry {m.record.transferred_to_registry}.
          </p>
        </div>
        <div className="text-right">
          <p className="text-2xl font-semibold tabular-nums">
            {m.score}
            <span className="text-sm font-normal text-stone-500">/100</span>
          </p>
          <p className="text-xs text-stone-600">{m.band}</p>
        </div>
      </div>
      <p className="text-xs text-stone-500">{scoreLabel}</p>

      <div>
        <h4 className="text-sm font-medium">Evidence path</h4>
        <ol className="mt-1 space-y-2">
          {m.features.map((f) => (
            <li key={f.key} className="rounded-lg border border-stone-200 p-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{f.label}</span>
                <span className="flex items-center gap-2">
                  <span className={cn('rounded-full px-2 py-0.5 text-xs', RESULT_STYLE[f.result])}>{f.result.replace('_', ' ')}</span>
                  <span className="tabular-nums text-xs text-stone-600">
                    +{f.points} of {f.max_points}
                  </span>
                </span>
              </div>
              <p className="mt-1 text-xs text-stone-600">
                Registry: {f.registry_value ?? 'not given'} → Profile: {f.profile_value ?? 'not given'}
              </p>
              <p className="text-xs text-stone-700">{f.detail}</p>
            </li>
          ))}
        </ol>
      </div>

      {m.explanation ? (
        <div className="rounded-lg bg-trust-50 p-3 text-sm">
          <p className="text-xs font-medium text-trust-800">{m.explanation.label}</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {m.explanation.statements.map((s, i) => (
              <li key={i}>{s.text}</li>
            ))}
          </ul>
          {m.explanation.missing.length > 0 && <p className="mt-1 text-xs text-stone-600">Missing: {m.explanation.missing.join(' ')}</p>}
        </div>
      ) : (
        !weak && <p className="text-xs text-stone-500">No AI explanation shown; the evidence above is complete.</p>
      )}

      {m.claims.length > 0 && (
        <div className="rounded-lg bg-saffron-50 p-3 text-sm">
          <p className="font-medium">Recovery claim being prepared (separate from your records)</p>
          <ol className="mt-1 list-decimal space-y-1 pl-5">
            {m.claims[0].checklist.map((c, i) => (
              <li key={i}>{c.step}</li>
            ))}
          </ol>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {m.status === 'candidate' && (
          <>
            <button className="btn btn-primary text-sm" onClick={() => act(m.id, 'open_claim')}>
              Prepare a recovery claim
            </button>
            <button className="btn btn-secondary text-sm" onClick={() => act(m.id, 'not_mine')}>
              Not a match
            </button>
          </>
        )}
        {m.status === 'not_mine' && <p className="text-sm text-stone-500">Marked as not a match.</p>}
      </div>
    </article>
  );
}

export default function RecoveryView({ caseId }: { caseId: string | null }) {
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const q = caseId ? `?case=${encodeURIComponent(caseId)}` : '';

  const load = useCallback(async () => {
    const r = await fetch(`/api/v1/recovery${q}`);
    if (!r.ok) return setError(r.status === 404 ? 'Recovery search is not available here.' : `Request failed (${r.status})`);
    setView(await r.json());
  }, [q]);
  useEffect(() => {
    load();
  }, [load]);

  const post = async (url: string, body?: object) => {
    setBusy(true);
    setError(null);
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) });
    setBusy(false);
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      setError(typeof d.detail === 'string' ? d.detail : (d.detail?.message ?? `Request failed (${r.status})`));
      return;
    }
    setView(await r.json());
  };
  const search = () => post('/api/v1/recovery/search', caseId ? { case_id: caseId } : {});
  const act = (id: string, decision: string) => post(`/api/v1/recovery/matches/${id}/decision`, caseId ? { decision, case_id: caseId } : { decision });

  if (error && !view) return <p className="card mt-6 text-sm">{error}</p>;
  if (!view) return <p className="mt-6 text-sm text-stone-500">Loading…</p>;
  const p = view.profile;
  const strong = view.matches.filter((m) => m.band !== 'weak evidence');
  const weak = view.matches.filter((m) => m.band === 'weak evidence');
  return (
    <div className="mt-6 space-y-6">
      <p className="rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-2 text-sm text-saffron-900">
        {view.notice} Registry: {view.registry.name}, version {view.registry.version} (synthetic, {view.registry.records} records).
      </p>
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}

      {p && (
        <section className="card space-y-2 text-sm">
          <h2 className="font-semibold">What is compared</h2>
          <p>
            Names: {[p.legal_name, ...p.name_variants].join(', ')} · Date of birth: {p.date_of_birth ?? 'not given'}
          </p>
          <ul className="list-disc pl-5 text-stone-600">
            {p.addresses.map((a, i) => (
              <li key={i}>
                {a.line} {a.pincode} ({a.from?.slice(0, 4) ?? '?'}–{a.to ? a.to.slice(0, 4) : 'now'})
              </li>
            ))}
          </ul>
          {!p.opted_in && p.editable_by_viewer && (
            <div className="space-y-2 rounded-lg bg-stone-100 p-3">
              <p>{p.consent_text}</p>
              <button className="btn btn-primary text-sm" disabled={busy} onClick={() => post('/api/v1/recovery/opt-in')}>
                I agree, turn on recovery search
              </button>
            </div>
          )}
          {!p.opted_in && !p.editable_by_viewer && <p className="text-stone-600">The account holder did not turn on recovery search.</p>}
          {p.opted_in && (
            <div className="flex flex-wrap gap-2 pt-1">
              <button className="btn btn-primary text-sm" disabled={busy} onClick={search}>
                {busy ? 'Checking…' : view.matches.length || view.ruled_out.length ? 'Check again' : 'Check the registry'}
              </button>
              {p.editable_by_viewer && (
                <button className="btn btn-secondary text-sm" disabled={busy} onClick={() => post('/api/v1/recovery/opt-out')}>
                  Withdraw and delete results
                </button>
              )}
            </div>
          )}
        </section>
      )}

      {strong.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Possible matches</h2>
          {strong.map((m) => (
            <MatchCard key={m.id} m={m} scoreLabel={view.score_label} act={act} />
          ))}
        </section>
      )}
      {weak.length > 0 && (
        <details className="card">
          <summary className="cursor-pointer text-sm font-medium">Weak evidence ({weak.length}): a name alone is not enough</summary>
          <div className="mt-3 space-y-3">
            {weak.map((m) => (
              <MatchCard key={m.id} m={m} scoreLabel={view.score_label} act={act} />
            ))}
          </div>
        </details>
      )}
      {view.ruled_out.length > 0 && (
        <section className="card text-sm">
          <h2 className="font-semibold">Ruled out by a conflict ({view.ruled_out.length})</h2>
          <p className="text-xs text-stone-500">A shared name does not mean a shared person, and says nothing about family.</p>
          <ul className="mt-2 space-y-1">
            {view.ruled_out.map((r) => (
              <li key={r.record_id}>
                {r.record_id} “{r.holder_name}”: {r.blocked_by.map((b) => REASON[b] ?? b).join(', ')}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
