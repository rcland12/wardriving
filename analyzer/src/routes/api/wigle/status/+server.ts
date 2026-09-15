import { json, type RequestHandler } from '@sveltejs/kit';
import { wigleStatus } from '$lib/server/review';

export const GET: RequestHandler = async ({ url }) => {
	return json(await wigleStatus(Number(url.searchParams.get('limit')) || 25));
};
