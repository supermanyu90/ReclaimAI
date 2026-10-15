import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import AnalyticsView from '@/components/AnalyticsView';

export const dynamic = 'force-dynamic';

export default async function AnalyticsPage() {
  if (!(await cookies()).get('reclaim_dev_user')) redirect('/signin');
  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Transaction trends</h1>
          <p className="text-sm text-stone-500">Twelve months of synthetic activity, and anything worth a second look.</p>
        </div>
        <a className="btn btn-secondary text-sm" href="/chat">Back to records</a>
      </header>
      <AnalyticsView />
    </main>
  );
}
