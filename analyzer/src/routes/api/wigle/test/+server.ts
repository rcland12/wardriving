import { json, type RequestHandler } from '@sveltejs/kit';
import { wigleTest } from '$lib/server/review';

export const GET: RequestHandler = async () => {
	return json(await wigleTest());
};
