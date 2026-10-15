import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import RecoveryView from '@/components/RecoveryView';

export const dynamic = 'force-dynamic';

export default async function RecoveryPage({ searchParams }: { searchParams: Promise<{ case?: string }> }) {
  if (!(await cookies()).get('reclaim_dev_user')) redirect('/signin');
  const { case: caseId } = await searchParams;
  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-saffron-800">Optional extension</p>
          <h1 className="text-2xl font-semibold">Unclaimed deposit check</h1>
          <p className="text-sm text-stone-500">Compare a profile with a registry of dormant deposits. Possible matches stay outside the inventory.</p>
        </div>
        <a className="btn btn-secondary text-sm" href={caseId ? '/family' : '/workspace'}>
          Back
        </a>
      </header>
      <RecoveryView caseId={caseId ?? null} />
    </main>
  );
}
