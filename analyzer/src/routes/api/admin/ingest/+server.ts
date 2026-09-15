import { json, type RequestHandler } from '@sveltejs/kit';
import { checkAdminAuth } from '$lib/server/adminAuth';
import { log } from '$lib/server/config';
import { ingestSession, reconcile } from '$lib/server/ingest';

/**
 * Called by the api container right after it stores an uploaded file:
 *
 *   POST /api/admin/ingest   {"session": "<name>", "demo": false}
 *
 * With no session, rescans the whole data directory. Not reachable from the internet (see
 * adminAuth.ts).
 */
export const POST: RequestHandler = async ({ request }) => {
	const auth = checkAdminAuth(request);
	if (!auth.ok) return json({ ok: false, error: auth.error }, { status: auth.status });

	const body = await request.json().catch(() => ({}));
	const session = typeof body?.session === 'string' ? body.session : '';
	const demo = body?.demo === true || body?.demo === '1' || body?.demo === 1;
	log('admin_ingest', { actor: auth.actor, session: session || '(all)', demo });

	const result = session ? await ingestSession(session, demo) : await reconcile('admin');
	return json({ ...result, action: 'wardrive.ingest' }, { status: result.ok ? 200 : 500 });
};
