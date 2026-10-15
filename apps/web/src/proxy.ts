/**
 * Hosted demo: everything sits behind a passcode when DEMO_PASSCODE is set. Locally it is
 * unset and this does nothing.
 */
import { NextRequest, NextResponse } from 'next/server';
import { GATE_COOKIE, gateValue } from '@/lib/gate';

export async function proxy(req: NextRequest) {
  const passcode = process.env.DEMO_PASSCODE;
  if (!passcode) return NextResponse.next();
  if (req.cookies.get(GATE_COOKIE)?.value === (await gateValue(passcode))) return NextResponse.next();
  if (req.nextUrl.pathname.startsWith('/api/')) return NextResponse.json({ detail: 'demo passcode required' }, { status: 401 });
  return NextResponse.redirect(new URL('/gate', req.url));
}

export const config = {
  // Everything except the gate itself and static assets.
  matcher: ['/((?!gate|_next/static|_next/image|favicon.ico).*)'],
};
