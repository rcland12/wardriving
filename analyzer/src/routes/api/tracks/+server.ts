import { json, type RequestHandler } from '@sveltejs/kit';
import { scopeOf, tracks } from '$lib/server/library';

export const GET: RequestHandler = async ({ url }) => {
	return json(await tracks(scopeOf(url)));
};
