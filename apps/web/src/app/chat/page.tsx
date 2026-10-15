import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import Chat from '@/components/Chat';
import TelegramLink from '@/components/TelegramLink';
import { getCapabilities } from '@/lib/api';
import { signOut } from '../signin/actions';

export const dynamic = 'force-dynamic';

export default async function ChatPage() {
  if (!(await cookies()).get('reclaim_dev_user')) redirect('/signin');
  const caps = await getCapabilities();
  const llm = caps?.capabilities.find((c) => c.name === 'llm') ?? null;

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Your records</h1>
          <p className="text-sm text-stone-500">Tell ReclaimAI about an asset. Nothing is recorded until you confirm.</p>
        </div>
        <div className="flex gap-2">
          <a className="btn btn-secondary text-sm" href="/workspace">Workspace</a>
          <a className="btn btn-secondary text-sm" href="/claims">Claim preparation</a>
          <a className="btn btn-secondary text-sm" href="/analytics">Trends</a>
          <form action={signOut}>
            <button className="btn btn-secondary text-sm" type="submit">Switch person</button>
          </form>
        </div>
      </header>
      <p className="mt-4 rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-2 text-sm text-saffron-900">
        Synthetic demo data. Institutions are fictional; no bank is connected.
      </p>
      <TelegramLink />
      <Chat llm={llm} />
    </main>
  );
}
