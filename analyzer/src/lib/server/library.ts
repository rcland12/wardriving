import path from 'node:path';
import type {
	DeviceDetail,
	DeviceRecord,
	DeviceSummary,
	DevicesResponse,
	Phy,
	ReviewRecord,
	SessionMeta,
	Security,
	TracksResponse
} from '$lib/types';
import { config, SESSION_RE } from './config';
import { db } from './db';
import { readRawRecord } from './kismet';

/** Read side: what the pages ask for, straight from Postgres. Ingest keeps it current. */

export type Scope = 'real' | 'demo';

export function scopeOf(url: URL): Scope {
	return url.searchParams.get('demo') === '1' ? 'demo' : 'real';
}

const isDemo = (scope: Scope) => scope === 'demo';
const num = (v: unknown) => Number(v ?? 0);
const str = (v: unknown) => (v == null ? '' : String(v));

export interface SessionRow {
	id: string;
	name: string;
	demo: boolean;
	kismetFile: string | null;
}

/** Every device in the scope merged across sessions, plus the sessions themselves. */
export async function devices(scope: Scope): Promise<DevicesResponse> {
	const pool = await db();
	const demo = isDemo(scope);
	const [sessionRows, deviceRows, reviewRows, uploadRows] = await Promise.all([
		pool.query(
			`select id, name, demo, start_ts, end_ts, packets, distance_m, kismet_size, uploaded_at
			 from sessions where demo = $1 order by start_ts, name`,
			[demo]
		),
		// Sessions in start order, so the first time a device appears is when it was discovered.
		pool.query(
			`select d.session_id, d.mac, d.phy, d.type, d.name, d.hidden, d.crypt, d.sec, d.wps, d.manuf,
			 d.channel, d.band, d.signal_max, d.lat, d.lon, d.first_ts, d.last_ts, d.packets
			 from devices d join sessions s on s.id = d.session_id
			 where s.demo = $1 order by s.start_ts, s.name`,
			[demo]
		),
		pool.query(
			`select r.session_id, r.reviewed_at, r.reviewed_by, r.fixes, r.rows, r.sha256
			 from reviews r join sessions s on s.id = r.session_id where s.demo = $1`,
			[demo]
		),
		pool.query(
			`select distinct on (session_name) session_name, uploaded_at, uploaded_by, transids, rows,
			 reviewed, donate, sha256
			 from wigle_uploads where demo = $1 order by session_name, uploaded_at desc`,
			[demo]
		)
	]);

	const index = new Map<string, number>();
	sessionRows.rows.forEach((r, i) => index.set(str(r.id), i));
	const stats = sessionRows.rows.map(() => ({
		wifi: 0,
		bt: 0,
		open: 0,
		hidden: 0,
		located: 0,
		fresh: 0
	}));
	const merged = new Map<string, DeviceSummary>();

	for (const r of deviceRows.rows) {
		const si = index.get(str(r.session_id))!;
		const st = stats[si];
		const phy = r.phy as Phy;
		const lat = num(r.lat);
		const lon = num(r.lon);
		const located = lat !== 0 || lon !== 0;
		if (phy === 'wifi') st.wifi++;
		else st.bt++;
		if (r.sec === 'OPEN') st.open++;
		if (r.hidden && phy === 'wifi') st.hidden++;
		if (located) st.located++;

		const id = `${phy}:${r.mac}`;
		const signal = num(r.signal_max);
		const cur = merged.get(id);
		if (!cur) {
			st.fresh++;
			merged.set(id, {
				id,
				mac: str(r.mac),
				phy,
				type: str(r.type),
				name: str(r.name),
				hidden: !!r.hidden,
				sec: r.sec as Security,
				crypt: str(r.crypt),
				wps: !!r.wps,
				manuf: str(r.manuf),
				channel: str(r.channel),
				band: str(r.band) as DeviceSummary['band'],
				signal,
				lat,
				lon,
				first: num(r.first_ts),
				last: num(r.last_ts),
				packets: num(r.packets),
				sessions: [si]
			});
			continue;
		}
		// Keep the strongest sighting's position and radio details; accumulate the rest.
		const stronger = signal !== 0 && (cur.signal === 0 || signal > cur.signal);
		if (stronger || (!cur.lat && !cur.lon && located)) {
			if (stronger) cur.signal = signal;
			cur.lat = lat;
			cur.lon = lon;
		}
		if (stronger) {
			cur.channel = str(r.channel);
			cur.band = str(r.band) as DeviceSummary['band'];
		}
		if (!cur.name && r.name) cur.name = str(r.name);
		if (cur.sec === 'UNKNOWN' && r.sec !== 'UNKNOWN') {
			cur.sec = r.sec as Security;
			cur.crypt = str(r.crypt);
		}
		cur.hidden = cur.hidden && !!r.hidden;
		cur.wps = cur.wps || !!r.wps;
		const first = num(r.first_ts);
		if (first && (!cur.first || first < cur.first)) cur.first = first;
		cur.last = Math.max(cur.last, num(r.last_ts));
		cur.packets += num(r.packets);
		if (!cur.sessions.includes(si)) cur.sessions.push(si);
	}

	const reviews = new Map(reviewRows.rows.map((r) => [str(r.session_id), r]));
	const uploads = new Map(uploadRows.rows.map((r) => [str(r.session_name), r]));
	const sessions: SessionMeta[] = sessionRows.rows.map((r, i) => {
		const review: ReviewRecord = {};
		const rv = reviews.get(str(r.id));
		if (rv) {
			review.reviewed = {
				at: new Date(rv.reviewed_at).toISOString(),
				by: rv.reviewed_by ?? undefined,
				fixes: rv.fixes,
				rows: num(rv.rows),
				sha256: str(rv.sha256)
			};
		}
		const up = uploads.get(str(r.name));
		if (up) {
			review.wigle = {
				at: new Date(up.uploaded_at).toISOString(),
				by: up.uploaded_by ?? undefined,
				transids: up.transids,
				rows: num(up.rows),
				reviewed: !!up.reviewed,
				donate: !!up.donate,
				sha256: str(up.sha256)
			};
		}
		return {
			name: str(r.name),
			demo: !!r.demo,
			start: num(r.start_ts),
			end: num(r.end_ts),
			wifi: stats[i].wifi,
			bt: stats[i].bt,
			open: stats[i].open,
			hidden: stats[i].hidden,
			located: stats[i].located,
			packets: num(r.packets),
			distance: num(r.distance_m),
			newDevices: stats[i].fresh,
			kismetBytes: num(r.kismet_size),
			uploadedAt: num(r.uploaded_at),
			review
		};
	});
	return { sessions, devices: [...merged.values()] };
}

export async function device(scope: Scope, phy: Phy, mac: string): Promise<DeviceDetail | null> {
	const pool = await db();
	const { rows } = await pool.query(
		`select s.name as session, d.* from devices d join sessions s on s.id = d.session_id
		 where s.demo = $1 and d.phy = $2 and d.mac = $3 order by s.start_ts`,
		[isDemo(scope), phy, mac]
	);
	if (!rows.length) return null;
	const records: DeviceRecord[] = rows.map((r) => ({
		session: str(r.session),
		type: str(r.type),
		name: str(r.name),
		hidden: !!r.hidden,
		crypt: str(r.crypt),
		sec: r.sec as Security,
		wps: !!r.wps,
		wpsName: str(r.wps_name),
		wpsModel: str(r.wps_model),
		manuf: str(r.manuf),
		channel: str(r.channel),
		frequency: num(r.frequency),
		band: str(r.band) as DeviceRecord['band'],
		htMode: str(r.ht_mode),
		maxRate: num(r.max_rate),
		country: str(r.country),
		mfp: str(r.mfp),
		signalMax: num(r.signal_max),
		signalMin: num(r.signal_min),
		lat: num(r.lat),
		lon: num(r.lon),
		alt: num(r.alt),
		avgLat: num(r.avg_lat),
		avgLon: num(r.avg_lon),
		first: num(r.first_ts),
		last: num(r.last_ts),
		packets: num(r.packets),
		uuids: Array.isArray(r.uuids) ? r.uuids : []
	}));
	const sightings = await pool.query(
		`select s.name as session, g.ts, g.lat, g.lon, g.signal
		 from sightings g join sessions s on s.id = g.session_id
		 where g.mac = $1 and g.session_id = any($2::bigint[]) order by g.ts`,
		[mac, rows.map((r) => r.session_id)]
	);
	return {
		id: `${phy}:${mac}`,
		mac,
		phy,
		records,
		sightings: sightings.rows.map((r) => ({
			session: str(r.session),
			ts: num(r.ts),
			lat: num(r.lat),
			lon: num(r.lon),
			signal: num(r.signal)
		}))
	};
}

/** The full Kismet record, read from the session file on demand (it isn't stored in Postgres). */
export async function raw(
	scope: Scope,
	phy: Phy,
	mac: string,
	session: string
): Promise<string | null> {
	const s = await sessionRow(scope, session);
	if (!s?.kismetFile) return null;
	return readRawRecord(path.join(config.dataDir, s.kismetFile), phy, mac);
}

/** Each session's route as [lon, lat] pairs, thinned to points at least ~8 m apart. */
export async function tracks(scope: Scope): Promise<TracksResponse> {
	const pool = await db();
	const { rows } = await pool.query(
		`select s.name, t.lat, t.lon from tracks t join sessions s on s.id = t.session_id
		 where s.demo = $1 order by s.start_ts, s.name, t.ts`,
		[isDemo(scope)]
	);
	const bySession = new Map<string, [number, number][]>();
	for (const r of rows) {
		const name = str(r.name);
		let points = bySession.get(name);
		if (!points) bySession.set(name, (points = []));
		const lon = num(r.lon);
		const lat = num(r.lat);
		const last = points[points.length - 1];
		if (last && Math.abs(last[0] - lon) < 0.0001 && Math.abs(last[1] - lat) < 0.00007) continue;
		points.push([lon, lat]);
	}
	const out: TracksResponse['tracks'] = [];
	for (const [session, points] of bySession) if (points.length > 1) out.push({ session, points });
	return { tracks: out };
}

/** A known session by exact name, or null. */
export async function sessionRow(scope: Scope, name: string): Promise<SessionRow | null> {
	if (!SESSION_RE.test(name)) return null;
	const { rows } = await (
		await db()
	).query('select id, name, demo, kismet_file from sessions where demo = $1 and name = $2', [
		isDemo(scope),
		name
	]);
	const r = rows[0];
	return r ? { id: str(r.id), name: str(r.name), demo: !!r.demo, kismetFile: r.kismet_file } : null;
}
