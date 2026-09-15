import { readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import type pg from 'pg';
import { config, DEMO_DIR, log, SESSION_RE } from './config';
import { db, transaction } from './db';
import { PARSER_VERSION, readKismet, trackDistance, type ParsedSession } from './kismet';

/**
 * Session files -> Postgres. Three things trigger it, all funnelling into the same code:
 *
 *   - the api container, right after it stores an upload (POST /api/admin/ingest)
 *   - a scan at startup and every WARDRIVE_SCAN_MINUTES, which catches anything missed while
 *     this container was down
 *   - the rescan buttons in the UI
 *
 * A session is (re)parsed only when its Kismet file's size or mtime differs from what was
 * ingested, or PARSER_VERSION has moved on. The session row is updated in place, so its review
 * survives; devices, sightings and tracks are replaced wholesale.
 */

export interface Found {
	name: string;
	demo: boolean;
	/** Relative to the data directory. */
	dir: string;
	kismetFile: string | null;
	size: number;
	mtime: number;
	uploadedAt: number;
}

export interface IngestResult {
	ok: boolean;
	ingested: string[];
	unchanged: number;
	removed: string[];
	errors: string[];
}

const label = (demo: boolean, name: string) => (demo ? `${DEMO_DIR}/${name}` : name);

async function scanSession(
	root: string,
	relDir: string,
	name: string,
	demo: boolean
): Promise<Found | null> {
	const dir = path.join(root, relDir);
	let files;
	try {
		files = await readdir(dir, { withFileTypes: true });
	} catch {
		return null;
	}
	let kismetFile: string | null = null;
	let size = 0;
	let mtime = 0;
	let uploadedAt = 0;
	for (const file of files) {
		// The api writes <file>.part-<thread> and renames it into place when complete.
		if (!file.isFile() || file.name.includes('.part-')) continue;
		const st = await stat(path.join(dir, file.name));
		uploadedAt = Math.max(uploadedAt, st.mtimeMs / 1000);
		const isGz = file.name === `${name}.kismet.gz`;
		if (isGz || (file.name === `${name}.kismet` && !kismetFile)) {
			kismetFile = path.join(relDir, file.name);
			size = st.size;
			mtime = st.mtimeMs;
		}
	}
	return { name, demo, dir: relDir, kismetFile, size, mtime, uploadedAt };
}

/** Every session directory. Throws if the data directory itself can't be read. */
async function scanAll(): Promise<Found[]> {
	const root = config.dataDir;
	const out: Found[] = [];
	for (const [rel, demo] of [
		['', false],
		[DEMO_DIR, true]
	] as const) {
		let entries;
		try {
			entries = await readdir(path.join(root, rel), { withFileTypes: true });
		} catch (err) {
			if (!demo) throw err; // no _demo folder is normal; no data folder is not
			continue;
		}
		for (const entry of entries) {
			if (!entry.isDirectory() || !SESSION_RE.test(entry.name)) continue;
			const found = await scanSession(root, path.join(rel, entry.name), entry.name, demo);
			if (found) out.push(found);
		}
	}
	return out;
}

interface Known {
	id: string;
	kismet_file: string | null;
	kismet_size: string | null;
	kismet_mtime: number | null;
	uploaded_at: number | null;
	parser_version: number;
}

function unchanged(found: Found, known: Known | undefined): boolean {
	return (
		!!known &&
		known.parser_version === PARSER_VERSION &&
		known.kismet_file === found.kismetFile &&
		Number(known.kismet_size ?? 0) === found.size &&
		Number(known.kismet_mtime ?? 0) === found.mtime
	);
}

async function insertParsed(client: pg.PoolClient, id: string, parsed: ParsedSession) {
	const d = parsed.devices;
	if (d.length) {
		// One statement per table: arrays in, unnest() spreads them into rows.
		await client.query(
			`insert into devices (session_id, phy, mac, type, name, hidden, crypt, sec, wps, wps_name,
				wps_model, manuf, channel, frequency, band, ht_mode, max_rate, country, mfp, signal_max,
				signal_min, lat, lon, alt, avg_lat, avg_lon, first_ts, last_ts, packets, uuids)
			select $1, phy, mac, type, name, hidden, crypt, sec, wps, wps_name, wps_model, manuf, channel,
				frequency, band, ht_mode, max_rate, country, mfp, signal_max, signal_min, lat, lon, alt,
				avg_lat, avg_lon, first_ts, last_ts, packets, uuids::jsonb
			from unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::bool[], $7::text[], $8::text[],
				$9::bool[], $10::text[], $11::text[], $12::text[], $13::text[], $14::int[], $15::text[],
				$16::text[], $17::real[], $18::text[], $19::text[], $20::int[], $21::int[], $22::float8[],
				$23::float8[], $24::real[], $25::float8[], $26::float8[], $27::float8[], $28::float8[],
				$29::bigint[], $30::text[])
			as t(phy, mac, type, name, hidden, crypt, sec, wps, wps_name, wps_model, manuf, channel,
				frequency, band, ht_mode, max_rate, country, mfp, signal_max, signal_min, lat, lon, alt,
				avg_lat, avg_lon, first_ts, last_ts, packets, uuids)
			on conflict do nothing`,
			[
				id,
				d.map((x) => x.phy),
				d.map((x) => x.mac),
				d.map((x) => x.type),
				d.map((x) => x.name),
				d.map((x) => x.hidden),
				d.map((x) => x.crypt),
				d.map((x) => x.sec),
				d.map((x) => x.wps),
				d.map((x) => x.wpsName),
				d.map((x) => x.wpsModel),
				d.map((x) => x.manuf),
				d.map((x) => x.channel),
				d.map((x) => Math.round(x.frequency)),
				d.map((x) => x.band),
				d.map((x) => x.htMode),
				d.map((x) => x.maxRate),
				d.map((x) => x.country),
				d.map((x) => x.mfp),
				d.map((x) => Math.round(x.signalMax)),
				d.map((x) => Math.round(x.signalMin)),
				d.map((x) => x.lat),
				d.map((x) => x.lon),
				d.map((x) => x.alt),
				d.map((x) => x.avgLat),
				d.map((x) => x.avgLon),
				d.map((x) => x.first),
				d.map((x) => x.last),
				d.map((x) => Math.round(x.packets)),
				d.map((x) => JSON.stringify(x.uuids))
			]
		);
	}
	const s = parsed.sightings;
	if (s.length) {
		await client.query(
			`insert into sightings (session_id, mac, ts, lat, lon, signal)
			select $1, * from unnest($2::text[], $3::float8[], $4::float8[], $5::float8[], $6::int[])`,
			[
				id,
				s.map((x) => x.mac),
				s.map((x) => x.ts),
				s.map((x) => x.lat),
				s.map((x) => x.lon),
				s.map((x) => Math.round(x.signal))
			]
		);
	}
	const t = parsed.track;
	if (t.length) {
		await client.query(
			`insert into tracks (session_id, ts, lat, lon, speed)
			select $1, * from unnest($2::float8[], $3::float8[], $4::float8[], $5::real[])`,
			[id, t.map((x) => x.ts), t.map((x) => x.lat), t.map((x) => x.lon), t.map((x) => x.speed)]
		);
	}
}

async function ingestFound(found: Found): Promise<'ingested' | 'unchanged'> {
	return transaction(async (client) => {
		// Two notifications for one session (the Pi sends the CSV and the database separately)
		// queue here instead of parsing twice.
		await client.query('select pg_advisory_xact_lock(hashtext($1))', [
			`wardrive:${label(found.demo, found.name)}`
		]);
		const { rows } = await client.query<Known>(
			`select id, kismet_file, kismet_size, kismet_mtime, uploaded_at, parser_version
			 from sessions where demo = $1 and name = $2 for update`,
			[found.demo, found.name]
		);
		const known = rows[0];
		if (unchanged(found, known)) {
			if (known.uploaded_at !== found.uploadedAt) {
				await client.query('update sessions set uploaded_at = $1 where id = $2', [
					found.uploadedAt,
					known.id
				]);
			}
			return 'unchanged';
		}

		const parsed: ParsedSession = found.kismetFile
			? await readKismet(path.join(config.dataDir, found.kismetFile))
			: { kismetVersion: '', devices: [], sightings: [], track: [] };
		let start = Infinity;
		let end = 0;
		for (const d of parsed.devices) {
			if (d.first > 0) start = Math.min(start, d.first);
			end = Math.max(end, d.last);
		}
		const values = [
			found.dir,
			found.kismetFile,
			found.size,
			found.mtime,
			found.uploadedAt,
			parsed.kismetVersion,
			Number.isFinite(start) ? start : 0,
			end,
			parsed.sightings.length,
			trackDistance(parsed.track),
			PARSER_VERSION
		];
		let id: string;
		if (known) {
			id = known.id;
			await client.query(
				`update sessions set dir = $1, kismet_file = $2, kismet_size = $3, kismet_mtime = $4,
				 uploaded_at = $5, kismet_version = $6, start_ts = $7, end_ts = $8, packets = $9,
				 distance_m = $10, parser_version = $11, ingested_at = now() where id = $12`,
				[...values, id]
			);
			for (const table of ['devices', 'sightings', 'tracks']) {
				await client.query(`delete from ${table} where session_id = $1`, [id]);
			}
		} else {
			const inserted = await client.query<{ id: string }>(
				`insert into sessions (dir, kismet_file, kismet_size, kismet_mtime, uploaded_at,
				 kismet_version, start_ts, end_ts, packets, distance_m, parser_version, demo, name)
				 values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) returning id`,
				[...values, found.demo, found.name]
			);
			id = inserted.rows[0].id;
		}
		await insertParsed(client, id, parsed);
		return 'ingested';
	});
}

let lastErrors: string[] = [];

/** Problems from the most recent scan, for the Sessions page. */
export function ingestErrors(): string[] {
	return lastErrors;
}

/** One session, by name. Used by the api's post-upload notification. */
export async function ingestSession(name: string, demo: boolean): Promise<IngestResult> {
	const result: IngestResult = { ok: true, ingested: [], unchanged: 0, removed: [], errors: [] };
	if (!SESSION_RE.test(name)) return { ...result, ok: false, errors: ['invalid session name'] };
	const rel = demo ? path.join(DEMO_DIR, name) : name;
	try {
		const found = await scanSession(config.dataDir, rel, name, demo);
		if (!found) {
			const removed = await (
				await db()
			).query('delete from sessions where demo = $1 and name = $2', [demo, name]);
			if (removed.rowCount) result.removed.push(label(demo, name));
			return result;
		}
		if ((await ingestFound(found)) === 'ingested') result.ingested.push(label(demo, name));
		else result.unchanged++;
	} catch (err) {
		result.ok = false;
		result.errors.push(`${label(demo, name)}: ${err instanceof Error ? err.message : String(err)}`);
	}
	log('ingest', { trigger: 'session', ...result });
	return result;
}

let running: Promise<IngestResult> | null = null;

/** Bring Postgres in line with the whole data directory. Concurrent callers share one run. */
export function reconcile(trigger: string): Promise<IngestResult> {
	running ??= doReconcile(trigger).finally(() => {
		running = null;
	});
	return running;
}

async function doReconcile(trigger: string): Promise<IngestResult> {
	const result: IngestResult = { ok: true, ingested: [], unchanged: 0, removed: [], errors: [] };
	let found: Found[];
	try {
		found = await scanAll();
	} catch (err) {
		// Never treat an unreadable mount as "every session was deleted".
		const message = `cannot read ${config.dataDir}: ${err instanceof Error ? err.message : String(err)}`;
		lastErrors = [message];
		log('ingest', { trigger, ok: false, errors: lastErrors });
		return { ...result, ok: false, errors: [message] };
	}
	const pool = await db();
	const { rows } = await pool.query<Known & { demo: boolean; name: string }>(
		'select id, demo, name, kismet_file, kismet_size, kismet_mtime, uploaded_at, parser_version from sessions'
	);
	const known = new Map(rows.map((r) => [label(r.demo, r.name), r]));
	const seen = new Set<string>();

	for (const f of found) {
		const key = label(f.demo, f.name);
		seen.add(key);
		const k = known.get(key);
		if (unchanged(f, k) && k!.uploaded_at === f.uploadedAt) {
			result.unchanged++;
			continue;
		}
		try {
			if ((await ingestFound(f)) === 'ingested') result.ingested.push(key);
			else result.unchanged++;
		} catch (err) {
			result.errors.push(`${key}: ${err instanceof Error ? err.message : String(err)}`);
		}
	}
	for (const [key, k] of known) {
		if (!seen.has(key)) {
			await pool.query('delete from sessions where id = $1', [k.id]);
			result.removed.push(key);
		}
	}
	result.ok = result.errors.length === 0;
	lastErrors = result.errors;
	if (
		result.ingested.length ||
		result.removed.length ||
		result.errors.length ||
		trigger === 'startup'
	) {
		log('ingest', { trigger, ...result });
	}
	return result;
}

let timer: ReturnType<typeof setInterval> | null = null;

/** Startup scan plus the periodic backstop. Safe to call more than once. */
export function startIngestion() {
	if (timer) return;
	const scan = (trigger: string) =>
		reconcile(trigger).catch((err) =>
			log('ingest_failed', { trigger, error: err instanceof Error ? err.message : String(err) })
		);
	scan('startup');
	timer = setInterval(() => scan('timer'), config.scanMinutes * 60_000);
	timer.unref();
}
