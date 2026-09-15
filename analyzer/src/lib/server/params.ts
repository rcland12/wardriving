import { error } from '@sveltejs/kit';
import type { Phy } from '$lib/types';

const MAC_RE = /^[0-9A-F]{2}(:[0-9A-F]{2}){5}$/;

export function parseDeviceParams(params: Partial<Record<string, string>>): {
	phy: Phy;
	mac: string;
} {
	const phy = params.phy;
	const mac = (params.mac ?? '').toUpperCase();
	if (phy !== 'wifi' && phy !== 'bt') error(400, 'phy must be wifi or bt');
	if (!MAC_RE.test(mac)) error(400, 'invalid MAC address');
	return { phy, mac };
}
