import pg from 'pg';
import { config, log } from './config';

/**
 * The analyzer's own database (`wardrive`, owned by the `wardrive` role) in rustyserver's shared
 * Postgres. The session files stay the source of truth; everything here except `reviews` and
 * `wigle_uploads` can be rebuilt from them by an ingest.
 *
 * Migrations run in order on first use and are recorded in schema_version. Only ever append.
 */
const MIGRATIONS: string[] = [
	`
	create table sessions (
		id              bigserial primary key,
		demo            boolean not null,
		name            text not null,
		-- Relative to WARDRIVE_DATA, so the mount point can change.
		dir             text not null,
		kismet_file     text,
		kismet_size     bigint,
		kismet_mtime    double precision,
		uploaded_at     double precision,
		kismet_version  text,
		start_ts        double precision,
		end_ts          double precision,
		packets         integer not null default 0,
		distance_m      double precision not null default 0,
		parser_version  integer not null,
		ingested_at     timestamptz not null default now(),
		unique (demo, name)
	);

	create table devices (
		session_id  bigint not null references sessions (id) on delete cascade,
		phy         text not null,
		mac         text not null,
		type        text, name text, hidden boolean, crypt text, sec text, wps boolean,
		wps_name    text, wps_model text, manuf text, channel text, frequency integer, band text,
		ht_mode     text, max_rate real, country text, mfp text,
		signal_max  integer, signal_min integer,
		lat         double precision, lon double precision, alt real,
		avg_lat     double precision, avg_lon double precision,
		first_ts    double precision, last_ts double precision,
		packets     bigint,
		uuids       jsonb not null default '[]',
		primary key (session_id, phy, mac)
	);
	create index devices_phy_mac on devices (phy, mac);

	create table sightings (
		session_id  bigint not null references sessions (id) on delete cascade,
		mac         text not null,
		ts          double precision not null,
		lat         double precision not null,
		lon         double precision not null,
		signal      integer not null
	);
	create index sightings_mac on sightings (mac);
	create index sightings_session on sightings (session_id);

	create table tracks (
		session_id  bigint not null references sessions (id) on delete cascade,
		ts          double precision not null,
		lat         double precision not null,
		lon         double precision not null,
		speed       real not null
	);
	create index tracks_session on tracks (session_id, ts);

	-- The repaired WiGLE CSV from wardrive_review.py fix. Survives re-ingests (the session row
	-- is updated in place), and goes away only if the session directory does.
	create table reviews (
		session_id   bigint primary key references sessions (id) on delete cascade,
		reviewed_at  timestamptz not null default now(),
		reviewed_by  text,
		fixes        jsonb not null,
		rows         integer not null,
		sha256       text not null,
		report       jsonb,
		-- bytea, not text: an SSID can contain a NUL byte, which Postgres text rejects.
		csv          bytea not null
	);

	-- Keyed by name, not session id: an upload to WiGLE is permanent, so its record must outlive
	-- anything that happens to the session row.
	create table wigle_uploads (
		id           bigserial primary key,
		demo         boolean not null,
		session_name text not null,
		uploaded_at  timestamptz not null default now(),
		uploaded_by  text,
		transids     jsonb not null,
		rows         integer not null,
		reviewed     boolean not null,
		donate       boolean not null,
		sha256       text
	);
	create index wigle_uploads_session on wigle_uploads (demo, session_name);
	`
];

let pool: pg.Pool | null = null;
let schema: Promise<void> | null = null;

function getPool(): pg.Pool {
	if (!pool) {
		pool = new pg.Pool({ ...config.postgres, max: 8, idleTimeoutMillis: 30_000 });
		pool.on('error', (err) => log('db_pool_error', { error: err.message }));
	}
	return pool;
}

async function migrate(p: pg.Pool) {
	const client = await p.connect();
	try {
		await client.query('begin');
		// Serialise concurrent boots; the lock is released at commit.
		await client.query("select pg_advisory_xact_lock(hashtext('wardrive:migrate'))");
		await client.query('create table if not exists schema_version (version integer not null)');
		const { rows } = await client.query(
			'select coalesce(max(version), 0) as v from schema_version'
		);
		const current = Number(rows[0].v);
		for (let v = current; v < MIGRATIONS.length; v++) {
			await client.query(MIGRATIONS[v]);
			await client.query('insert into schema_version values ($1)', [v + 1]);
			log('db_migrated', { version: v + 1 });
		}
		await client.query('commit');
	} catch (err) {
		await client.query('rollback').catch(() => {});
		throw err;
	} finally {
		client.release();
	}
}

/** The pool, with the schema brought up to date first. Retries the migration after a failure. */
export async function db(): Promise<pg.Pool> {
	const p = getPool();
	schema ??= migrate(p).catch((err) => {
		schema = null;
		throw err;
	});
	await schema;
	return p;
}

/** Runs fn in a transaction on one connection. */
export async function transaction<T>(fn: (client: pg.PoolClient) => Promise<T>): Promise<T> {
	const client = await (await db()).connect();
	try {
		await client.query('begin');
		const result = await fn(client);
		await client.query('commit');
		return result;
	} catch (err) {
		await client.query('rollback').catch(() => {});
		throw err;
	} finally {
		client.release();
	}
}
