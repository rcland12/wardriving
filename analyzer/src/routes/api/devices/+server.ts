import { json, type RequestHandler } from '@sveltejs/kit';
import { reconcile } from '$lib/server/ingest';
import { devices, scopeOf } from '$lib/server/library';

export const GET: RequestHandler = async ({ url }) => {
	if (url.searchParams.has('refresh')) await reconcile('manual');
	return json(await devices(scopeOf(url)));
};
