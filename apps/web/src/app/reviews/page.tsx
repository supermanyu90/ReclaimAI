import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import ReviewerView from '@/components/ReviewerView';
import { signOut } from '../signin/actions';

export const dynamic = 'force-dynamic';

export default async function ReviewsPage() {
  if (!(await cookies()).get('reclaim_dev_user')) redirect('/signin');
  return (
    <main className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Handover reviews</h1>
          <p className="text-sm text-stone-500">Two independent reviewers must approve the current evidence before anything is released.</p>
        </div>
        <form action={signOut}>
          <button className="btn btn-secondary text-sm" type="submit">Switch person</button>
        </form>
      </header>
      <ReviewerView />
    </main>
  );
}
