import { backendFetch } from '@/lib/backend';
import { signInAs } from './actions';

export const dynamic = 'force-dynamic';

interface DevUser {
  id: string;
  display_name: string;
  role: string;
  tenant: string;
}

async function getDevUsers(): Promise<DevUser[] | null> {
  try {
    const res = await backendFetch('/v1/dev/users');
    if (!res.ok) return null;
    return (await res.json()).users;
  } catch {
    return null;
  }
}

export default async function SignIn() {
  const users = await getDevUsers();
  return (
    <main className="mx-auto max-w-xl px-4 py-12 sm:px-6">
      <h1 className="text-3xl font-semibold">Local demo sign-in</h1>
      <p className="mt-2 text-stone-600">
        Pick a synthetic person. This stands in for the identity provider and only works in local
        mode; cloud mode uses Identity Platform.
      </p>
      {users === null ? (
        <p className="card mt-6 text-sm">API unreachable or not in local mode. Start it with <code>make dev</code>.</p>
      ) : (
        <ul className="mt-6 space-y-3">
          {users.map((u) => (
            <li key={u.id}>
              <form action={signInAs}>
                <input type="hidden" name="user_id" value={u.id} />
                <input type="hidden" name="role" value={u.role} />
                <button type="submit" className="card flex w-full items-center justify-between text-left">
                  <span>
                    <span className="block font-medium">{u.display_name}</span>
                    <span className="block text-sm text-stone-500">{u.tenant}</span>
                  </span>
                  <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs text-stone-700">{u.role}</span>
                </button>
              </form>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
