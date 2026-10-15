'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

interface Asset {
  id: string;
  label: string;
  institution_name: string | null;
  jurisdiction: string;
  masked_reference: string | null;
  currency: string | null;
  amounts: Record<string, number>;
  source_date: string | null;
  verification_state: string;
  nomination_status: string;
}
interface Person {
  owner_user_id: string;
  owner: string;
  designated: boolean;
  handover: { id: string; state: string } | null;
  before_release: { sections: string[]; contacts?: { name: string; role: string }[]; document_locations?: string[] };
  after_release?: {
    assets: Asset[];
    evidence_index: { title: string; kind: string; source_date: string | null; supports: string }[];
    passport_id: string | null;
    claim_checklists: boolean;
    totals: Record<string, Record<string, number>>;
  };
  status_text?: string;
  simulated_verification: string;
}
interface Case {
  id: string;
  state: string;
  evidence: { document_id: string; title: string; status: string }[];
  approvals_current: number;
  approvals_required: number;
}
interface Checklist {
  status: string;
  referral: string | null;
  paths: { key: string; title: string; condition: string | null; citation: { ref: string }; items: { id: string; requirement: string; status: string }[] }[];
}

const STATE_TEXT: Record<string, string> = {
  REQUESTED: 'Requested: add documents, then submit for review.',
  VERIFICATION_PENDING: 'Waiting for two independent reviewers.',
  NEEDS_INFORMATION: 'Reviewers asked for more information.',
  HOLD: 'On hold while reviewers check a discrepancy.',
  APPROVED: 'Approved.',
  RELEASED: 'Released.',
  REJECTED: 'Rejected. You may start a new request.',
};
const money = (minor: number, cur: string | null) =>
  cur === 'INR' ? `₹${(minor / 100).toLocaleString('en-IN')}` : `${cur === 'SGD' ? 'S$' : ''}${(minor / 100).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;

function CaseControls({ person, onChange }: { person: Person; onChange: () => void }) {
  const [c, setC] = useState<Case | null>(null);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const load = useCallback(async () => {
    if (!person.handover) return;
    const r = await fetch(`/api/v1/handovers/${person.handover.id}`);
    if (r.ok) setC(await r.json());
  }, [person.handover]);
  useEffect(() => {
    load();
  }, [load]);

  const call = async (url: string, init?: RequestInit) => {
    setError(null);
    const r = await fetch(url, init);
    if (!r.ok) setError((await r.json().catch(() => ({})))?.detail?.message ?? `Request failed (${r.status})`);
    onChange();
    load();
  };
  const upload = async (f: File) => {
    const form = new FormData();
    form.append('file', f);
    await call(`/api/v1/handovers/${person.handover!.id}/evidence`, { method: 'POST', body: form });
  };

  if (!person.designated) return <p className="text-sm text-stone-500">You are not designated to receive these records.</p>;
  if (!person.handover || person.handover.state === 'REJECTED')
    return (
      <button
        className="btn btn-primary text-sm"
        onClick={() =>
          call('/api/v1/handovers', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ owner_user_id: person.owner_user_id }) })
        }
      >
        Request handover
      </button>
    );
  const open = ['REQUESTED', 'NEEDS_INFORMATION', 'HOLD', 'VERIFICATION_PENDING'].includes(person.handover.state);
  return (
    <div className="space-y-2 text-sm">
      <p>
        <span className="font-medium">Handover:</span> {STATE_TEXT[person.handover.state]}
        {c && person.handover.state === 'VERIFICATION_PENDING' ? ` (${c.approvals_current} of ${c.approvals_required} approvals)` : ''}
      </p>
      {c && c.evidence.length > 0 && (
        <ul className="list-disc pl-5 text-stone-600">
          {c.evidence.map((e) => (
            <li key={e.document_id}>
              {e.title} · {e.status.replace(/_/g, ' ')}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <div className="flex flex-wrap gap-2">
          <input ref={file} type="file" className="sr-only" aria-label="Add a document" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <button className="btn btn-secondary text-sm" onClick={() => file.current?.click()}>
            Add a document
          </button>
          {['REQUESTED', 'NEEDS_INFORMATION'].includes(person.handover.state) && (
            <button className="btn btn-primary text-sm" onClick={() => call(`/api/v1/handovers/${person.handover!.id}/submit`, { method: 'POST' })}>
              Submit for review
            </button>
          )}
        </div>
      )}
      {error && <p className="text-red-700">{error}</p>}
      <p className="text-xs text-stone-500">{person.simulated_verification}</p>
    </div>
  );
}

function Claim({ asset }: { asset: Asset }) {
  const [res, setRes] = useState<Checklist | null>(null);
  const run = async () => {
    const r = await fetch('/api/v1/family/claims', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ asset_id: asset.id, survivorship: 'unknown', dispute: 'unknown' }),
    });
    if (r.ok) setRes(await r.json());
  };
  if (!res)
    return (
      <button className="text-trust-700 underline" onClick={run}>
        Prepare claim checklist
      </button>
    );
  if (res.status !== 'ready') return <p className="text-saffron-800">{res.referral}</p>;
  return (
    <div className="mt-1 space-y-1">
      {res.paths.map((p) => (
        <div key={p.key}>
          <p className="font-medium">
            {p.title} <span className="text-xs text-stone-500">[{p.citation.ref}]</span>
            {p.condition ? <span className="text-xs text-stone-500"> · only if {p.condition}</span> : null}
          </p>
          <p className="text-xs text-stone-600">{p.items.map((i) => `${i.requirement} (${i.status})`).join('; ')}</p>
        </div>
      ))}
    </div>
  );
}

export default function FamilyView() {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    const r = await fetch('/api/v1/family');
    if (!r.ok) return setError('This page is for designated family members.');
    const body = await r.json();
    setPeople(body.people);
    setNotice(body.notice);
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  if (error) return <p className="card mt-6 text-sm">{error}</p>;
  if (!people) return <p className="mt-6 text-sm text-stone-500">Loading…</p>;
  return (
    <div className="mt-6 space-y-6">
      <p className="rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-2 text-sm text-saffron-900">Synthetic demo. {notice}</p>
      {people.map((p) => (
        <section key={p.owner_user_id} className="card space-y-4">
          <h2 className="text-lg font-semibold">{p.owner}</h2>
          {p.status_text && <p className="rounded-lg bg-trust-50 px-3 py-2 text-sm font-medium text-trust-900">{p.status_text}</p>}
          <CaseControls person={p} onChange={load} />

          {!p.after_release && (
            <div className="text-sm">
              <h3 className="font-medium">Shared with you now</h3>
              {p.before_release.contacts && (
                <p className="mt-1 text-stone-600">Contacts: {p.before_release.contacts.map((c) => `${c.name} (${c.role})`).join(', ') || 'none'}</p>
              )}
              {p.before_release.document_locations && (
                <ul className="mt-1 list-disc pl-5 text-stone-600">
                  {p.before_release.document_locations.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              )}
              <p className="mt-1 text-xs text-stone-500">Asset details become visible only after a reviewed release.</p>
            </div>
          )}

          {p.after_release && (
            <div className="space-y-4 text-sm">
              <div className="overflow-x-auto">
                <h3 className="font-medium">Records shared with you (read only)</h3>
                <table className="mt-1 w-full min-w-[32rem]">
                  <tbody className="divide-y divide-stone-100 align-top">
                    {p.after_release.assets.map((a) => (
                      <tr key={a.id}>
                        <td className="py-2">
                          <span className="block font-medium">{a.label}</span>
                          <span className="text-xs text-stone-500">
                            {a.institution_name} · {a.masked_reference ?? 'no reference'} · as of {a.source_date ?? 'unknown'} ·{' '}
                            {a.verification_state.replace(/_/g, ' ')} · nomination {a.nomination_status.replace(/_/g, ' ')}
                          </span>
                          {p.after_release!.claim_checklists && <Claim asset={a} />}
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          {Object.values(a.amounts).map((v, i) => (
                            <span key={i} className="block">
                              {money(v, a.currency)}
                            </span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {p.after_release.evidence_index.length > 0 && (
                <div>
                  <h3 className="font-medium">Supporting documents</h3>
                  <ul className="mt-1 list-disc pl-5 text-stone-600">
                    {p.after_release.evidence_index.map((d, i) => (
                      <li key={i}>
                        {d.title} ({d.kind.replace(/_/g, ' ')}, {d.source_date ?? 'undated'}) · supports {d.supports}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <p>
                {p.after_release.passport_id ? (
                  <a className={cn('text-trust-700 underline')} href={`/api/v1/passports/${p.after_release.passport_id}/download`}>
                    Download your Family Asset Passport
                  </a>
                ) : (
                  'No passport export has been prepared for you yet.'
                )}
              </p>
              {p.handover && (
                <p className="text-sm">
                  <a className="text-trust-700 underline" href={`/recovery?case=${p.handover.id}`}>
                    Optional: check an unclaimed-deposit registry for {p.owner}
                  </a>
                </p>
              )}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
