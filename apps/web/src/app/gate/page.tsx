import { unlock } from './actions';

export const dynamic = 'force-dynamic';

export default async function Gate({ searchParams }: { searchParams: Promise<{ wrong?: string }> }) {
  const { wrong } = await searchParams;
  return (
    <main className="mx-auto max-w-md px-4 py-16 sm:px-6">
      <h1 className="text-2xl font-semibold">ReclaimAI demo</h1>
      <p className="mt-2 text-sm text-stone-600">
        A hosted demo with synthetic data only. Enter the passcode you were given.
      </p>
      <form action={unlock} className="mt-6 space-y-3">
        <input
          name="passcode"
          type="password"
          autoComplete="off"
          required
          aria-label="Passcode"
          className="w-full rounded-lg border border-stone-300 px-3 py-2"
        />
        {wrong && <p className="text-sm text-red-700">That passcode is not right.</p>}
        <button type="submit" className="btn btn-primary w-full">
          Continue
        </button>
      </form>
    </main>
  );
}
