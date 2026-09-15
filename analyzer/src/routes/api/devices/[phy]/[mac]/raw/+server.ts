import { error, type RequestHandler } from '@sveltejs/kit';
import { raw, scopeOf } from '$lib/server/library';
import { parseDeviceParams } from '$lib/server/params';

export const GET: RequestHandler = async ({ params, url }) => {
	const { phy, mac } = parseDeviceParams(params);
	const record = await raw(scopeOf(url), phy, mac, url.searchParams.get('session') ?? '');
	if (record === null) error(404, 'record not found');
	return new Response(record, { headers: { 'Content-Type': 'application/json' } });
};
