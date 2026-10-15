'use client';

import { useEffect, useState } from 'react';

interface Status {
  linked: boolean;
  linked_at: string | null;
}
interface Code {
  code: string;
  expires_at: string;
  bot_username: string | null;
}

export default function TelegramLink() {
  const [status, setStatus] = useState<Status | null>(null);
  const [code, setCode] = useState<Code | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    const res = await fetch('/api/v1/channels/telegram');
    if (res.ok) setStatus(await res.json());
  };
  useEffect(() => {
    refresh();
  }, []);

  const issue = async () => {
    setBusy(true);
    const res = await fetch('/api/v1/channels/telegram/link-code', { method: 'POST' });
    setBusy(false);
    if (res.ok) setCode(await res.json());
  };
  const unlink = async () => {
    setBusy(true);
    await fetch('/api/v1/channels/telegram/unlink', { method: 'POST' });
    setBusy(false);
    setCode(null);
    refresh();
  };

  if (!status) return null;
  return (
    <details className="mt-3 rounded-xl border border-stone-200 bg-white px-4 py-2 text-sm">
      <summary className="cursor-pointer font-medium">
        Telegram: {status.linked ? 'linked' : 'not linked'}
      </summary>
      <div className="mt-2 space-y-2 text-stone-600">
        <p>
          Telegram shows only short, masked summaries. Amounts, documents and confirmation stay here in the
          signed-in web app.
        </p>
        {status.linked ? (
          <button className="btn btn-secondary text-sm" disabled={busy} onClick={unlink}>Unlink Telegram</button>
        ) : code ? (
          <p>
            In a private chat with {code.bot_username ? `@${code.bot_username}` : 'the ReclaimAI bot'}, send{' '}
            <code className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-stone-900">/link {code.code}</code>. Single use,
            expires at {new Date(code.expires_at).toLocaleTimeString()}.
          </p>
        ) : (
          <button className="btn btn-secondary text-sm" disabled={busy} onClick={issue}>Link Telegram</button>
        )}
      </div>
    </details>
  );
}
