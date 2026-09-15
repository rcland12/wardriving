import { createReadStream, createWriteStream } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { pipeline } from 'node:stream/promises';
import { createGunzip } from 'node:zlib';
import { bandOf, classifyCrypt } from '$lib/security';
import type { DeviceRecord, Phy, Sighting, TrackPoint } from '$lib/types';

/** What the analyzer keeps from one Kismet database. */
export interface ParsedDevice extends Omit<DeviceRecord, 'session'> {
	mac: string;
	phy: Phy;
}

export interface ParsedSession {
	kismetVersion: string;
	devices: ParsedDevice[];
	sightings: (Omit<Sighting, 'session'> & { mac: string })[];
	track: TrackPoint[];
}

/** Bump when what's extracted changes; every session is then re-ingested on the next scan. */
export const PARSER_VERSION = 1;

const PHY: Record<string, Phy> = { 'IEEE802.11': 'wifi', Bluetooth: 'bt' };

type Json = Record<string, unknown>;

function obj(value: unknown): Json {
	return value && typeof value === 'object' && !Array.isArray(value) ? (value as Json) : {};
}

function str(value: unknown): string {
	const text = typeof value === 'string' ? value : value == null ? '' : String(value);
	// Postgres text can't hold NUL, and some SSIDs and Bluetooth names contain one.
	return text.includes('\u0000') ? text.replaceAll('\u0000', '') : text;
}

function num(value: unknown): number {
	const n = typeof value === 'number' ? value : Number(value);
	return Number.isFinite(n) ? n : 0;
}

/** Kismet geopoints are [lon, lat]; (0, 0) means no fix. */
function geopoint(loc: unknown): [number, number] | null {
	const point = obj(loc)['kismet.common.location.geopoint'];
	if (!Array.isArray(point) || point.length < 2) return null;
	const [lon, lat] = point.map(num);
	return lat === 0 && lon === 0 ? null : [lat, lon];
}

function decode(blob: unknown): string {
	if (typeof blob === 'string') return blob;
	if (blob instanceof Uint8Array) return new TextDecoder().decode(blob);
	return '';
}

function parseDevice(row: Json): ParsedDevice | null {
	const phy = PHY[str(row.phyname)];
	if (!phy) return null;
	const raw = decode(row.device);
	let d: Json;
	try {
		d = obj(JSON.parse(raw));
	} catch {
		return null;
	}
	const base = (key: string) => d['kismet.device.base.' + key];
	const signal = obj(base('signal'));
	const location = obj(base('location'));

	let name = '';
	let crypt = str(base('crypt'));
	let wps = false;
	let wpsName = '';
	let wpsModel = '';
	let htMode = '';
	let maxRate = 0;
	let country = '';
	let mfp = '';
	let hidden = false;
	let uuids: string[] = [];

	if (phy === 'wifi') {
		const record = obj(obj(d['dot11.device'])['dot11.device.last_beaconed_ssid_record']);
		const ssid = (key: string) => record['dot11.advertisedssid.' + key];
		name = str(ssid('ssid'));
		crypt = str(ssid('crypt_string')) || crypt;
		wps = ssid('wps_state') != null;
		wpsName = str(ssid('wps_device_name'));
		wpsModel = [str(ssid('wps_manuf')), str(ssid('wps_model_name')), str(ssid('wps_model_number'))]
			.filter(Boolean)
			.join(' ');
		htMode = str(ssid('ht_mode'));
		maxRate = num(ssid('maxrate'));
		country = str(ssid('dot11d_country'));
		if (num(ssid('wpa_mfp_required'))) mfp = 'required';
		else if (num(ssid('wpa_mfp_supported'))) mfp = 'supported';
		hidden = num(ssid('cloaked')) === 1 || name === '';
	} else {
		name = str(base('name'));
		const bt = obj(d['bluetooth.device']);
		const vec = bt['bluetooth.device.service_uuid_vec'];
		uuids = Array.isArray(vec) ? vec.map(str).filter(Boolean) : [];
	}

	// Wi-Fi APs record where their signal peaked: the best guess at where they are.
	const peak = geopoint(signal['kismet.common.signal.peak_loc']);
	const avg = geopoint(location['kismet.common.location.avg_loc']);
	const avgLat = num(row.avg_lat) || avg?.[0] || 0;
	const avgLon = num(row.avg_lon) || avg?.[1] || 0;
	const [lat, lon] = peak ?? [avgLat, avgLon];
	const signalMax = num(row.strongest_signal) || num(signal['kismet.common.signal.max_signal']);
	const frequency = num(base('frequency'));

	return {
		mac: str(row.devmac).toUpperCase(),
		phy,
		type: str(row.type) || str(base('type')),
		name,
		hidden,
		crypt,
		sec: classifyCrypt(crypt, phy),
		wps,
		wpsName,
		wpsModel,
		manuf: str(base('manuf')),
		channel: str(base('channel')),
		frequency,
		band: bandOf(frequency, phy),
		htMode,
		maxRate,
		country,
		mfp,
		signalMax,
		signalMin: num(signal['kismet.common.signal.min_signal']),
		lat,
		lon,
		alt: num(obj(signal['kismet.common.signal.peak_loc'])['kismet.common.location.alt']),
		avgLat,
		avgLon,
		first: num(row.first_time),
		last: num(row.last_time),
		packets: num(base('packets.total')),
		uuids
	};
}

function hasTable(db: DatabaseSync, name: string): boolean {
	return !!db.prepare("select 1 from sqlite_master where type = 'table' and name = ?").get(name);
}

/** GPS positions over time from snapshots and packets, one per second, oldest first. */
function readTrack(db: DatabaseSync): TrackPoint[] {
	const bySecond = new Map<number, TrackPoint>();
	if (hasTable(db, 'packets')) {
		for (const r of db
			.prepare('select ts_sec, lat, lon, speed from packets where lat != 0 or lon != 0')
			.all()) {
			const ts = num(r.ts_sec);
			if (!bySecond.has(ts)) {
				bySecond.set(ts, { ts, lat: num(r.lat), lon: num(r.lon), speed: num(r.speed) });
			}
		}
	}
	if (hasTable(db, 'snapshots')) {
		for (const r of db
			.prepare('select ts_sec, lat, lon from snapshots where lat != 0 or lon != 0')
			.all()) {
			const ts = num(r.ts_sec);
			if (!bySecond.has(ts)) bySecond.set(ts, { ts, lat: num(r.lat), lon: num(r.lon), speed: -1 });
		}
	}
	return [...bySecond.values()].sort((a, b) => a.ts - b.ts);
}

export function readKismetFile(file: string): ParsedSession {
	const db = new DatabaseSync(file, { readOnly: true });
	try {
		const version = hasTable(db, 'KISMET')
			? str(db.prepare('select kismet_version from KISMET').get()?.kismet_version)
			: '';
		const devices: ParsedDevice[] = [];
		for (const row of db
			.prepare(
				'select devmac, phyname, type, strongest_signal, avg_lat, avg_lon, first_time, last_time, device from devices'
			)
			.all()) {
			const device = parseDevice(row);
			if (device) devices.push(device);
		}
		const known = new Set(devices.map((d) => d.mac));
		const sightings: ParsedSession['sightings'] = [];
		if (hasTable(db, 'packets')) {
			// With AP-only tracking the packets are beacons, so the source MAC is the device.
			for (const r of db
				.prepare(
					'select ts_sec, ts_usec, sourcemac, lat, lon, signal from packets where lat != 0 or lon != 0'
				)
				.all()) {
				const mac = str(r.sourcemac).toUpperCase();
				if (!known.has(mac)) continue;
				sightings.push({
					mac,
					ts: num(r.ts_sec) + num(r.ts_usec) / 1e6,
					lat: num(r.lat),
					lon: num(r.lon),
					signal: num(r.signal)
				});
			}
		}
		return { kismetVersion: version, devices, sightings, track: readTrack(db) };
	} finally {
		db.close();
	}
}

/** Kismet databases are stored gzipped; SQLite needs a real file, so unpack to a temp dir. */
async function withKismetFile<T>(kismetFile: string, fn: (file: string) => T): Promise<T> {
	if (!kismetFile.endsWith('.gz')) return fn(kismetFile);
	const dir = await mkdtemp(path.join(tmpdir(), 'wardrive-analyzer-'));
	try {
		const file = path.join(dir, 'session.kismet');
		await pipeline(createReadStream(kismetFile), createGunzip(), createWriteStream(file));
		return fn(file);
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
}

export function readKismet(kismetFile: string): Promise<ParsedSession> {
	return withKismetFile(kismetFile, readKismetFile);
}

/**
 * One device's full Kismet JSON record. Read from the session file on demand rather than
 * indexed: the records are most of a database's size and only ever viewed one at a time.
 */
export function readRawRecord(kismetFile: string, phy: Phy, mac: string): Promise<string | null> {
	const phyname = Object.keys(PHY).find((k) => PHY[k] === phy);
	return withKismetFile(kismetFile, (file) => {
		const db = new DatabaseSync(file, { readOnly: true });
		try {
			const row = db
				.prepare('select device from devices where phyname = ? and upper(devmac) = ?')
				.get(phyname ?? '', mac);
			return row ? decode(row.device) : null;
		} finally {
			db.close();
		}
	});
}

const EARTH_M = 6371008.8;

export function haversine(aLat: number, aLon: number, bLat: number, bLon: number): number {
	const rad = Math.PI / 180;
	const dLat = (bLat - aLat) * rad;
	const dLon = (bLon - aLon) * rad;
	const h =
		Math.sin(dLat / 2) ** 2 + Math.cos(aLat * rad) * Math.cos(bLat * rad) * Math.sin(dLon / 2) ** 2;
	return 2 * EARTH_M * Math.asin(Math.sqrt(h));
}

/** Metres along the track, skipping GPS glitches faster than a car can go. */
export function trackDistance(track: TrackPoint[]): number {
	let total = 0;
	for (let i = 1; i < track.length; i++) {
		const a = track[i - 1];
		const b = track[i];
		const d = haversine(a.lat, a.lon, b.lat, b.lon);
		const dt = Math.max(1, b.ts - a.ts);
		if (d / dt < 70) total += d;
	}
	return total;
}
