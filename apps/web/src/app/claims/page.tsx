import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import ClaimsPreview from '@/components/ClaimsPreview';

export const dynamic = 'force-dynamic';

export default async function ClaimsPage() {
  if (!(await cookies()).get('reclaim_dev_user')) redirect('/signin');
  return (
    <main className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Claim preparation</h1>
          <p className="text-sm text-stone-500">What a family would need to prepare, from approved and dated policy.</p>
        </div>
        <a className="btn btn-secondary text-sm" href="/chat">Back to records</a>
      </header>
      <div className="mt-4 rounded-xl border border-trust-200 bg-trust-50 px-4 py-3 text-sm text-trust-900">
        <p className="font-medium">Inventory access is not claim entitlement.</p>
        <p>
          Seeing a record here does not mean anyone is entitled to it. Institutions decide claims and make payments;
          this checklist only helps gather documents.
        </p>
      </div>
      <p className="mt-3 rounded-xl border border-saffron-200 bg-saffron-50 px-4 py-2 text-sm text-saffron-900">
        Synthetic demo. The only policy here is fictional and is labelled as such.
      </p>
      <ClaimsPreview />
    </main>
  );
}
