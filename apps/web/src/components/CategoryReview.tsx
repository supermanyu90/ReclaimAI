'use client';

import { useEffect, useState } from 'react';

const CATEGORIES = ['income', 'investment_contribution', 'insurance_premium', 'loan_payment', 'transfer', 'other'];
const LABEL: Record<string, string> = {
  income: 'Income',
  investment_contribution: 'Investment contribution',
  insurance_premium: 'Insurance premium',
  loan_payment: 'Loan payment',
  transfer: 'Transfer',
  other: 'Other',
};

interface Item {
  transaction_id: string;
  account: string;
  narrative: string;
  amount: string;
  booked_on: string;
  predicted_category: string | null;
  confidence: number | null;
  source: string;
  model_version: string;
  confirmed_category: string | null;
}
interface Queue {
  needs_review: Item[];
  recent: Item[];
  predicted: number;
  abstained: number;
  model_versions: string[];
  notice: string;
  classifier: { state: string; adapter: string; detail: string };
}

export default function CategoryReview() {
  const [q, setQ] = useState<Queue | null>(null);
  const [choice, setChoice] = useState<Record<string, string>>({});

  const load = () =>
    fetch('/api/v1/analytics/review')
      .then((r) => (r.ok ? r.json() : null))
      .then(setQ);
  useEffect(() => {
    load();
  }, []);

  const confirm = async (id: string) => {
    const category = choice[id];
    if (!category) return;
    const res = await fetch(`/api/v1/analytics/transactions/${id}/category`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    });
    if (res.ok) load();
  };

  if (!q) return null;
  return (
    <section className="card text-sm">
      <h2 className="text-lg font-semibold">Transaction categories</h2>
      <p className="mt-1 text-stone-600">
        {q.predicted} categorised by {q.classifier.adapter === 'rules-only' ? 'keyword rules (no trained model)' : q.model_versions.join(', ')};{' '}
        {q.abstained} held back as uncertain. {q.notice}
      </p>
      <h3 className="mt-4 font-medium">Needs your review ({q.needs_review.length})</h3>
      {q.needs_review.length === 0 && <p className="mt-1 text-stone-500">Nothing waiting.</p>}
      <ul className="mt-2 divide-y divide-stone-200">
        {q.needs_review.map((i) => (
          <li key={i.transaction_id} className="flex flex-wrap items-center gap-2 py-2">
            <span className="min-w-0 flex-1">
              <span className="block font-mono text-xs">{i.narrative}</span>
              <span className="text-stone-500">
                {i.amount} · {i.booked_on} · confidence {i.confidence ?? 'n/a'}
              </span>
            </span>
            <select
              className="input-field h-10 w-auto"
              value={choice[i.transaction_id] ?? ''}
              onChange={(e) => setChoice((c) => ({ ...c, [i.transaction_id]: e.target.value }))}
              aria-label="Category"
            >
              <option value="">Choose…</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {LABEL[c]}
                </option>
              ))}
            </select>
            <button className="btn btn-secondary text-sm" disabled={!choice[i.transaction_id]} onClick={() => confirm(i.transaction_id)}>
              Confirm
            </button>
          </li>
        ))}
      </ul>
      <h3 className="mt-4 font-medium">Least certain suggestions</h3>
      <ul className="mt-2 divide-y divide-stone-100">
        {q.recent.map((i) => (
          <li key={i.transaction_id} className="flex flex-wrap justify-between gap-2 py-1.5">
            <span className="font-mono text-xs">{i.narrative}</span>
            <span className="text-stone-600">
              {LABEL[i.predicted_category ?? ''] ?? '—'} ({i.confidence ?? i.source}){i.confirmed_category ? ` · confirmed ${LABEL[i.confirmed_category]}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
