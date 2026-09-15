import { json, type RequestHandler } from '@sveltejs/kit';
import { ingestErrors, reconcile } from '$lib/server/ingest';
import { devices, scopeOf } from '$lib/server/library';

export const GET: RequestHandler = async ({ url }) => {
	if (url.searchParams.has('refresh')) await reconcile('manual');
	const { sessions } = await devices(scopeOf(url));
	const errors = ingestErrors();
	return json({ sessions, indexError: errors.length ? errors.join('; ') : null });
};
