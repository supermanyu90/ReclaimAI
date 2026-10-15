'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Capability } from '@/lib/api';
import { cn } from '@/lib/utils';

type ChatState = 'ok' | 'clarification' | 'review' | 'unavailable' | 'invalid_output' | 'blocked';

interface Source {
  field: string;
  source: 'user_message' | 'owner_edit' | 'assumption' | 'document' | string;
  quote?: string;
  note?: string;
  page?: number | null;
  confidence?: number | null;
}
interface Conflict {
  kind: string;
  field?: string;
  label?: string;
  recorded?: unknown;
  proposed?: unknown;
  draft_display?: string;
  document_display?: string;
  note?: string;
}
interface Doc {
  id: string;
  title: string;
  kind: string;
  status: 'pending' | 'processed' | 'needs_review' | 'failed';
  asset_type: string | null;
  review_reasons: string[];
  possible_duplicates: { label: string }[];
  duplicate_of: string | null;
}
interface FieldRow {
  field: string;
  label: string;
  value: string | null;
  raw: unknown;
  sources: Source[];
}
interface Proposal {
  id: string;
  status: string;
  change_kind: 'create' | 'update' | 'close';
  target: { label: string; institution_name: string | null; masked_reference: string | null } | null;
  fields: FieldRow[];
  missing_fields: string[];
  conflicts: Conflict[];
  unresolved_conflicts: Conflict[];
  documents?: string[];
  model_version: string | null;
  can_confirm: boolean;
}
interface Turn {
  id: string;
  speaker: 'owner' | 'assistant';
  text: string;
  state?: ChatState | null;
}

async function api<T>(path: string, init?: RequestInit): Promise<{ ok: boolean; status: number; body: T }> {
  const res = await fetch(`/api/v1/${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  const body = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body: body as T };
}

const EDITABLE: Record<string, { label: string; kind: 'text' | 'money' | 'date' | 'months' }> = {
  institution_name: { label: 'Institution', kind: 'text' },
  balance_minor: { label: 'Amount', kind: 'money' },
  term_months: { label: 'Term (months)', kind: 'months' },
  opened_on: { label: 'Opened on', kind: 'date' },
};

function SourceChip({ s }: { s: Source }) {
  const page = s.page ? ` p.${s.page}` : '';
  const conf = s.confidence != null ? ` · read with ${Math.round(s.confidence * 100)}% confidence` : '';
  const text =
    s.source === 'document'
      ? `from document${page}${s.quote ? `: “${s.quote}”` : ''}${conf}`
      : s.source === 'user_message'
      ? `from your message${s.quote ? `: “${s.quote}”` : ''}`
      : s.source === 'owner_edit'
        ? 'edited by you'
        : s.source === 'assumption'
          ? 'assumed, please check'
          : s.source;
  return (
    <span
      className={cn(
        'mr-1 inline-block rounded-full px-2 py-0.5 text-xs',
        s.source === 'assumption' ? 'bg-saffron-100 text-saffron-800' : 'bg-stone-100 text-stone-600',
      )}
      title={s.note}
    >
      {text}
      {s.note && s.source !== 'assumption' ? ' · ' + s.note : ''}
    </span>
  );
}

function ReviewCard({ proposal, onChange }: { proposal: Proposal; onChange: (p: Proposal | null, notice?: string) => void }) {
  const keyRef = useRef<string>(crypto.randomUUID()); // reused on retry: one key per proposal
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ asset_version: number; replayed: boolean; detail?: { message: string } }>(
        `proposals/${proposal.id}/confirm`,
        { method: 'POST', headers: { 'Idempotency-Key': keyRef.current } },
      );
      if (r.ok) onChange(null, `Recorded (version ${r.body.asset_version}). Owner-reported; not verified by the institution.`);
      else setError(r.body.detail?.message ?? `Could not confirm (${r.status}).`);
    } catch {
      setError('Network problem. Your confirmation can be retried safely.');
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (field: string, use: 'draft' | 'document') => {
    setBusy(true);
    const r = await api<Proposal>(`proposals/${proposal.id}/resolve`, { method: 'POST', body: JSON.stringify({ field, use }) });
    setBusy(false);
    if (r.ok) onChange(r.body);
  };

  const cancel = async () => {
    setBusy(true);
    const r = await api<Proposal>(`proposals/${proposal.id}/cancel`, { method: 'POST' });
    setBusy(false);
    if (r.ok) onChange(null, 'Draft cancelled. Nothing was recorded.');
  };

  const saveEdit = async () => {
    const changes: Record<string, unknown> = {};
    for (const [field, value] of Object.entries(draft)) {
      const kind = EDITABLE[field].kind;
      if (value.trim() === '') continue;
      changes[field] = kind === 'money' ? Math.round(Number(value.replace(/,/g, '')) * 100) : kind === 'months' ? Number(value) : value.trim();
    }
    setBusy(true);
    const r = await api<Proposal & { detail?: { message: string } }>(`proposals/${proposal.id}`, { method: 'PATCH', body: JSON.stringify(changes) });
    setBusy(false);
    if (r.ok) {
      setEditing(false);
      onChange(r.body);
    } else setError(r.body.detail?.message ?? 'Those values were not accepted.');
  };

  const title =
    proposal.change_kind === 'close'
      ? `Close “${proposal.target?.label}”?`
      : proposal.change_kind === 'update'
        ? `Update “${proposal.target?.label}”`
        : 'New asset (draft)';

  return (
    <section className="card mt-4 border-trust-200" aria-label="Review proposed change">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        <span className="text-xs text-stone-500">
          {proposal.status === 'draft' ? 'Draft: details missing' : 'Awaiting your confirmation'}
        </span>
      </div>

      {proposal.fields.length > 0 && (
        <dl className="mt-3 divide-y divide-stone-200 text-sm">
          {proposal.fields.map((f) => (
            <div key={f.field} className="grid gap-1 py-2 sm:grid-cols-[10rem_1fr]">
              <dt className="text-stone-500">{f.label}</dt>
              <dd>
                {editing && EDITABLE[f.field] ? (
                  <input
                    className="input-field h-10"
                    type={EDITABLE[f.field].kind === 'date' ? 'date' : 'text'}
                    defaultValue={EDITABLE[f.field].kind === 'money' && typeof f.raw === 'number' ? String(f.raw / 100) : String(f.raw ?? '')}
                    onChange={(e) => setDraft((d) => ({ ...d, [f.field]: e.target.value }))}
                    aria-label={EDITABLE[f.field].label}
                  />
                ) : f.value ? (
                  <span className="font-medium">{f.value}</span>
                ) : (
                  <span className="rounded bg-saffron-100 px-1.5 text-saffron-800">Needed</span>
                )}
                <div className="mt-1">{f.sources.map((s, i) => <SourceChip key={i} s={s} />)}</div>
              </dd>
            </div>
          ))}
        </dl>
      )}

      {proposal.unresolved_conflicts.length > 0 && (
        <div className="mt-3 space-y-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">
          <p className="font-medium">A document disagrees with this draft. Choose the right value before confirming.</p>
          {proposal.unresolved_conflicts.map((c) => (
            <div key={c.field} className="flex flex-wrap items-center gap-2">
              <span className="w-full sm:w-auto">{c.label}:</span>
              <button className="btn btn-secondary text-sm" disabled={busy} onClick={() => resolve(c.field!, 'draft')}>
                Keep {c.draft_display} (draft)
              </button>
              <button className="btn btn-secondary text-sm" disabled={busy} onClick={() => resolve(c.field!, 'document')}>
                Use {c.document_display} (document)
              </button>
            </div>
          ))}
        </div>
      )}

      {proposal.conflicts.some((c) => c.kind !== 'document_conflict') && (
        <ul className="mt-3 space-y-1 rounded-lg bg-saffron-50 p-3 text-sm text-saffron-900">
          {proposal.conflicts
            .filter((c) => c.kind !== 'document_conflict')
            .map((c, i) => (
              <li key={i}>
                {c.kind === 'possible_duplicate'
                  ? `Possible duplicate of “${c.label}”. ${c.note ?? ''}`
                  : c.kind === 'document_needs_review'
                    ? `Document needs human review: ${c.note}`
                    : `${c.field}: recorded ${String(c.recorded)}, now ${String(c.proposed)}`}
              </li>
            ))}
        </ul>
      )}

      <p className="mt-3 text-xs text-stone-500">
        Drafted by {proposal.model_version ?? 'you'}. Confirming records what you reported. A document shows what it says, not
        that it is genuine; nothing is verified with the institution.
      </p>
      {error && <p className="mt-2 text-sm text-red-700" role="alert">{error}</p>}

      <div className="mt-4 flex flex-wrap gap-2">
        <button className="btn btn-primary" disabled={!proposal.can_confirm || busy || editing} onClick={confirm}>
          {busy ? 'Working…' : 'Confirm'}
        </button>
        {proposal.change_kind === 'create' &&
          (editing ? (
            <button className="btn btn-secondary" disabled={busy} onClick={saveEdit}>Save edits</button>
          ) : (
            <button className="btn btn-secondary" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
          ))}
        <button className="btn btn-secondary" disabled={busy} onClick={cancel}>Cancel</button>
      </div>
    </section>
  );
}

const DOC_STATUS: Record<Doc['status'], string> = {
  pending: 'Reading…',
  processed: 'Read',
  needs_review: 'Needs human review',
  failed: 'Could not be processed',
};
const NOT_ASSET_KINDS = ['birth_certificate', 'death_certificate', 'other'];

function DocumentCard({ doc, canAttach, onAttach }: { doc: Doc; canAttach: boolean; onAttach: () => void }) {
  return (
    <div className="card mt-3 py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">{doc.title}</span>
        <span className={cn('rounded-full px-2 py-0.5 text-xs', doc.status === 'processed' ? 'bg-trust-100 text-trust-800' : 'bg-saffron-100 text-saffron-800')}>
          {DOC_STATUS[doc.status]}
        </span>
      </div>
      <p className="mt-1 text-stone-500">
        {doc.kind.replace(/_/g, ' ')}
        {doc.duplicate_of ? ' · same file as an earlier upload' : ''}
      </p>
      {doc.review_reasons.length > 0 && <p className="mt-1 text-saffron-800">{doc.review_reasons.join('; ')}</p>}
      {doc.possible_duplicates.length > 0 && (
        <p className="mt-1 text-saffron-800">
          Possibly already recorded as “{doc.possible_duplicates[0].label}”. Nothing is merged automatically.
        </p>
      )}
      {canAttach && (
        <button className="btn btn-secondary mt-2 text-sm" onClick={onAttach}>
          Add to draft
        </button>
      )}
    </div>
  );
}

export default function Chat({ llm }: { llm: Capability | null }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [text, setText] = useState('');
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<{ tone: 'warn' | 'info'; text: string } | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch('/api/v1/documents', { method: 'POST', body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setBanner({ tone: 'warn', text: typeof body.detail === 'string' ? body.detail : 'Upload failed.' });
      return;
    }
    let doc: Doc = body;
    setDocs((d) => [doc, ...d]);
    for (let i = 0; i < 60 && doc.status === 'pending'; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const r = await api<Doc>(`documents/${doc.id}`);
      if (r.ok) doc = r.body;
    }
    setDocs((d) => d.map((x) => (x.id === doc.id ? doc : x)));
  };

  const attachDoc = async (doc: Doc) => {
    if (!proposal) return;
    const r = await api<Proposal & { detail?: { message?: string } }>(`proposals/${proposal.id}/documents`, {
      method: 'POST',
      body: JSON.stringify({ document_id: doc.id }),
    });
    if (r.ok) setProposal(r.body);
    else setBanner({ tone: 'warn', text: r.body.detail?.message ?? 'That document could not be added.' });
  };
  const endRef = useRef<HTMLDivElement>(null);

  const loadHistory = useCallback(async () => {
    const r = await api<{ turns: (Turn & { proposal_id: string | null })[] }>('chat/history');
    if (!r.ok) return;
    setTurns(r.body.turns);
    const last = [...r.body.turns].reverse().find((t) => t.proposal_id);
    if (last?.proposal_id) {
      const p = await api<Proposal>(`proposals/${last.proposal_id}`);
      if (p.ok && ['draft', 'awaiting_confirmation'].includes(p.body.status)) setProposal(p.body);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, pending]);

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const message = text.trim();
    if (!message || pending) return;
    setText('');
    setBanner(null);
    setTurns((t) => [...t, { id: crypto.randomUUID(), speaker: 'owner', text: message }]);
    setPending(true);
    try {
      const r = await api<{ state: ChatState; reply: string; proposal: Proposal | null; detail?: string }>('chat/messages', {
        method: 'POST',
        body: JSON.stringify({ text: message }),
      });
      if (!r.ok) {
        setBanner({ tone: 'warn', text: typeof r.body.detail === 'string' ? r.body.detail : `Request failed (${r.status}). Nothing was changed.` });
        return;
      }
      setTurns((t) => [...t, { id: crypto.randomUUID(), speaker: 'assistant', text: r.body.reply, state: r.body.state }]);
      setProposal(r.body.proposal && ['draft', 'awaiting_confirmation'].includes(r.body.proposal.status) ? r.body.proposal : null);
      if (r.body.state === 'unavailable' || r.body.state === 'invalid_output') setBanner({ tone: 'warn', text: r.body.reply });
    } catch {
      setBanner({ tone: 'warn', text: 'Network problem. Nothing was changed.' });
    } finally {
      setPending(false);
    }
  };

  const modelLabel = llm
    ? llm.state === 'live'
      ? `Model: ${llm.detail}`
      : llm.state === 'fixture'
        ? `Rules fixture: ${llm.detail}`
        : `Model unavailable: ${llm.detail}`
    : 'Model status unknown';

  return (
    <div className="mt-4 flex flex-1 flex-col">
      <p className={cn('text-xs', llm?.state === 'unavailable' ? 'text-red-700' : 'text-stone-500')}>{modelLabel}</p>
      {banner && (
        <p role="status" className={cn('mt-3 rounded-xl px-4 py-2 text-sm', banner.tone === 'warn' ? 'bg-red-50 text-red-800' : 'bg-trust-50 text-trust-800')}>
          {banner.text}
        </p>
      )}

      <ol className="mt-4 flex-1 space-y-3" aria-live="polite">
        {turns.length === 0 && (
          <li className="text-sm text-stone-500">Try: “I opened an SBI FD for ₹5 lakh for 3 years”.</li>
        )}
        {turns.map((t) => (
          <li key={t.id} className={cn('flex', t.speaker === 'owner' ? 'justify-end' : 'justify-start')}>
            <p
              className={cn(
                'max-w-[85%] whitespace-pre-line rounded-2xl px-4 py-2 text-sm',
                t.speaker === 'owner' ? 'bg-trust-600 text-white' : 'bg-white shadow-sm ring-1 ring-stone-200',
                t.state === 'blocked' && 'ring-red-300',
              )}
            >
              {t.text}
            </p>
          </li>
        ))}
        {pending && (
          <li className="flex justify-start">
            <p className="rounded-2xl bg-white px-4 py-2 text-sm text-stone-500 ring-1 ring-stone-200">Processing…</p>
          </li>
        )}
        <div ref={endRef} />
      </ol>

      {docs.map((d) => (
        <DocumentCard
          key={d.id}
          doc={d}
          canAttach={
            !!proposal &&
            ['processed', 'needs_review'].includes(d.status) &&
            !NOT_ASSET_KINDS.includes(d.kind) &&
            !(proposal.documents ?? []).includes(d.id)
          }
          onAttach={() => attachDoc(d)}
        />
      ))}

      {proposal && (
        <ReviewCard
          key={proposal.id}
          proposal={proposal}
          onChange={(p, notice) => {
            setProposal(p);
            if (notice) setBanner({ tone: 'info', text: notice });
          }}
        />
      )}

      <form onSubmit={send} className="sticky bottom-0 mt-4 flex gap-2 bg-stone-50 py-3">
        <label htmlFor="chat-input" className="sr-only">Message</label>
        <input
          id="chat-input"
          className="input-field"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Describe an asset or a change…"
          maxLength={1000}
          disabled={pending}
          autoComplete="off"
        />
        <button className="btn btn-primary" type="submit" disabled={pending || !text.trim()}>
          Send
        </button>
        <input
          ref={fileRef}
          type="file"
          className="sr-only"
          accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.txt,application/pdf,image/*,text/plain"
          aria-label="Upload a document"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload(f);
            e.target.value = '';
          }}
        />
        <button type="button" className="btn btn-secondary" onClick={() => fileRef.current?.click()}>
          Upload
        </button>
      </form>
      <p className="text-xs text-stone-500">
        Never type OTPs, PINs, passwords or full account numbers. Uploaded documents stay private to you.
      </p>
    </div>
  );
}
