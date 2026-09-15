import { error, json, type RequestHandler } from '@sveltejs/kit';
import { device, scopeOf } from '$lib/server/library';
import { parseDeviceParams } from '$lib/server/params';

export const GET: RequestHandler = async ({ params, url }) => {
	const { phy, mac } = parseDeviceParams(params);
	const detail = await device(scopeOf(url), phy, mac);
	if (!detail) error(404, 'device not found');
	return json(detail);
};
