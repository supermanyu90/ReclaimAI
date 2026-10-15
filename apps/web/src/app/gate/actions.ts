'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { GATE_COOKIE, gateValue } from '@/lib/gate';

export async function unlock(formData: FormData) {
  const expected = process.env.DEMO_PASSCODE;
  const given = String(formData.get('passcode') ?? '');
  if (!expected) redirect('/signin');
  if ((await gateValue(given)) !== (await gateValue(expected))) redirect('/gate?wrong=1');
  (await cookies()).set(GATE_COOKIE, await gateValue(expected), { httpOnly: true, sameSite: 'lax', secure: true, path: '/', maxAge: 60 * 60 * 12 });
  redirect('/signin');
}
