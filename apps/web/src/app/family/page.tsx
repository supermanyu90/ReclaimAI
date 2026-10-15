import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import FamilyView from '@/components/FamilyView';
import { signOut } from '../signin/actions';

export const dynamic = 'force-dynamic';

export default async function FamilyPage() {
  if (!(await cookies()).get('reclaim_dev_user')) redirect('/signin');
  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Family access</h1>
          <p className="text-sm text-stone-500">What a family member has chosen to share with you, and how to request the rest.</p>
        </div>
        <form action={signOut}>
          <button className="btn btn-secondary text-sm" type="submit">Switch person</button>
        </form>
      </header>
      <FamilyView />
    </main>
  );
}
