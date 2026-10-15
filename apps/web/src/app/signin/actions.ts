'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

const UUID = /^[0-9a-f-]{36}$/i;

const HOME: Record<string, string> = { owner: '/workspace', family: '/family', reviewer: '/reviews' };

export async function signInAs(formData: FormData) {
  const id = String(formData.get('user_id') ?? '');
  const role = String(formData.get('role') ?? '');
  if (!UUID.test(id)) return;
  // Local demo only: the API accepts dev identities for synthetic users in local mode.
  (await cookies()).set('reclaim_dev_user', id, { httpOnly: true, sameSite: 'lax', path: '/' });
  redirect(HOME[role] ?? '/workspace');
}

export async function signOut() {
  (await cookies()).delete('reclaim_dev_user');
  redirect('/signin');
}
