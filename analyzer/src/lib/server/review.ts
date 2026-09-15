import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { config, log } from './config';
import { db } from './db';
import { sessionRow, type Scope } from './library';

/**
 * Check, fix and WiGLE upload stay in tools/wardrive_review.py, so there is one implementation.
 *
 * The session directory is mounted read-only, so the tool runs with --no-record and
 * --reviewed-csv pointing into a temp dir: the reviewed copy is loaded from Postgres into that
 * file before a check or upload, and saved back to Postgres after a fix. Upload records live in
 * Postgres too. Arguments go in as an array (no shell), and only exact, known session names
 * are ever passed.
 */

export type ReviewAction = 'check' | 'fix' | 'dry-run' | 'upload';

type ToolResult = Record<string, unknown> & { ok: boolean; error?: string };

function run(args: string[], timeout: number): Promise<ToolResult> {
	return new Promise((resolve) => {
		execFile(
			config.python,
			[config.reviewTool, '--data', config.dataDir, '--json', ...args],
			// The WiGLE credentials reach the tool through this environment, and nowhere else.
			{ timeout, maxBuffer: 16 * 1024 * 1024, env: process.env },
			(error, stdout, stderr) => {
				// Exit code 1 still carries a JSON result (a FAIL verdict, a blocked upload).
				const line = stdout.trim().split('\n').pop() ?? '';
				try {
					resolve(JSON.parse(line));
				} catch {
					const detail = (stderr || error?.message || 'no output')
						.trim()
						.split('\n')
						.slice(-3)
						.join(' ');
					resolve({ ok: false, error: `review tool failed: ${detail}` });
				}
			}
		);
	});
}

const busy = new Set<string>();

export async function review(
	scope: Scope,
	name: string,
	action: ReviewAction,
	user: string | null
) {
	const session = await sessionRow(scope, name);
	if (!session) return { ok: false, error: 'unknown session' };
	if (action === 'upload' && session.demo) {
		return { ok: false, error: 'demo sessions can never be uploaded to WiGLE' };
	}
	const pool = await db();
	if (action === 'upload' || action === 'dry-run') {
		const { rows } = await pool.query(
			'select uploaded_at from wigle_uploads where demo = $1 and session_name = $2 limit 1',
			[session.demo, session.name]
		);
		if (rows.length) {
			return {
				ok: true,
				session: session.name,
				sent: false,
				blocked: 'uploaded',
				message: `already uploaded (${new Date(rows[0].uploaded_at).toISOString()})`
			};
		}
	}

	const key = `${scope}/${name}`;
	if (busy.has(key))
		return { ok: false, error: 'another action is already running on this session' };
	busy.add(key);
	const work = await mkdtemp(path.join(tmpdir(), 'wardrive-review-'));
	try {
		const csvPath = path.join(work, `${session.name}.wiglecsv`);
		if (action !== 'fix') {
			const { rows } = await pool.query('select csv from reviews where session_id = $1', [
				session.id
			]);
			if (rows.length) await writeFile(csvPath, rows[0].csv);
		}
		const base = [...(session.demo ? ['--demo'] : []), '--reviewed-csv', csvPath, '--no-record'];
		const command =
			action === 'dry-run'
				? ['wigle', session.name, '--dry-run']
				: action === 'upload'
					? ['wigle', session.name]
					: [action, session.name];
		const result = await run([...base, ...command], action === 'upload' ? 330_000 : 120_000);

		if (action === 'fix' && result.ok) {
			const csv = await readFile(csvPath);
			await pool.query(
				`insert into reviews (session_id, reviewed_by, fixes, rows, sha256, report, csv)
				 values ($1, $2, $3, $4, $5, $6, $7)
				 on conflict (session_id) do update set reviewed_at = now(), reviewed_by = excluded.reviewed_by,
				 fixes = excluded.fixes, rows = excluded.rows, sha256 = excluded.sha256,
				 report = excluded.report, csv = excluded.csv`,
				[
					session.id,
					user,
					JSON.stringify(result.fixes ?? []),
					Number(result.rows ?? 0),
					createHash('sha256').update(csv).digest('hex'),
					JSON.stringify(result.report ?? null),
					csv
				]
			);
		}
		if (action === 'upload' && result.ok && result.sent) {
			await pool.query(
				`insert into wigle_uploads (demo, session_name, uploaded_by, transids, rows, reviewed, donate, sha256)
				 values ($1, $2, $3, $4, $5, $6, $7, $8)`,
				[
					session.demo,
					session.name,
					user,
					JSON.stringify(result.transids ?? []),
					Number(result.rows ?? 0),
					!!result.reviewed,
					!!result.donate,
					result.sha256 ?? null
				]
			);
		}
		if (action === 'fix' || action === 'upload') {
			log('review', {
				action,
				session: session.name,
				demo: session.demo,
				user,
				ok: result.ok,
				sent: result.sent ?? undefined,
				error: result.error
			});
		}
		return result;
	} finally {
		busy.delete(key);
		await rm(work, { recursive: true, force: true });
	}
}

export function wigleStatus(limit = 25) {
	return run(['wigle-status', '--limit', String(Math.max(1, Math.min(100, limit)))], 60_000);
}

export function wigleTest() {
	return run(['wigle-test'], 30_000);
}
