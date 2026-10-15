import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import Workspace from '@/components/Workspace';
import { getCapabilities } from '@/lib/api';

export const dynamic = 'force-dynamic';

export default async function WorkspacePage() {
  if (!(await cookies()).get('reclaim_dev_user')) redirect('/signin');
  const recovery = (await getCapabilities())?.optional_features?.recovery ?? false;
  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Workspace</h1>
          <p className="text-sm text-stone-500">Everything recorded, how it connects, and what your family would see.</p>
        </div>
        <nav className="flex flex-wrap gap-2">
          <a className="btn btn-secondary text-sm" href="/chat">Chat</a>
          <a className="btn btn-secondary text-sm" href="/claims">Claim preparation</a>
          <a className="btn btn-secondary text-sm" href="/analytics">Trends</a>
          {recovery && (
            <a className="btn btn-secondary text-sm" href="/recovery">
              Unclaimed deposits (optional)
            </a>
          )}
        </nav>
      </header>
      <p className="mt-4 rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-2 text-sm text-saffron-900">
        Synthetic demo data. Recorded information only: this does not establish completeness, ownership or entitlement.
      </p>
      <Workspace />
    </main>
  );
}
