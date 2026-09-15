import type { DeviceDetail, DevicesResponse, TracksResponse } from './types';

/** Browser-side cache: the device list is fetched once per dataset and shared by every page. */

const devices = new Map<string, Promise<DevicesResponse>>();
const tracks = new Map<string, Promise<TracksResponse>>();

async function getJson<T>(url: string): Promise<T> {
	const res = await fetch(url);
	if (!res.ok) {
		const body = await res.json().catch(() => null);
		throw new Error(body?.message ?? `${url}: HTTP ${res.status}`);
	}
	return res.json();
}

export function demoQuery(demo: boolean): string {
	return demo ? '?demo=1' : '';
}

export function loadDevices(demo: boolean, refresh = false): Promise<DevicesResponse> {
	const key = String(demo);
	if (refresh || !devices.has(key)) {
		const url = `/api/devices${demo ? '?demo=1' : ''}${refresh ? (demo ? '&' : '?') + 'refresh=1' : ''}`;
		const promise = getJson<DevicesResponse>(url);
		promise.catch(() => devices.delete(key));
		devices.set(key, promise);
		tracks.delete(key);
	}
	return devices.get(key)!;
}

export function loadTracks(demo: boolean): Promise<TracksResponse> {
	const key = String(demo);
	if (!tracks.has(key)) {
		const promise = getJson<TracksResponse>(`/api/tracks${demoQuery(demo)}`);
		promise.catch(() => tracks.delete(key));
		tracks.set(key, promise);
	}
	return tracks.get(key)!;
}

export function loadDevice(demo: boolean, id: string): Promise<DeviceDetail> {
	const [phy, ...mac] = id.split(':');
	return getJson<DeviceDetail>(`/api/devices/${phy}/${mac.join(':')}${demoQuery(demo)}`);
}

export function invalidateDevices() {
	devices.clear();
	tracks.clear();
}

export async function postJson<T>(url: string, body: unknown = {}): Promise<T> {
	const res = await fetch(url, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
	const data = await res.json().catch(() => null);
	if (!res.ok) throw new Error(data?.message ?? `HTTP ${res.status}`);
	return data as T;
}
