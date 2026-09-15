import { env } from '$env/dynamic/private';
import path from 'node:path';

/**
 * Every setting comes from the environment (the container's env_file in rustyserver's compose).
 * Read lazily, so a missing value fails the request that needs it rather than the whole server.
 */
export const config = {
	/** The home API's session directory, mounted read-only. Demo uploads live in its _demo/. */
	get dataDir(): string {
		return path.resolve(env.WARDRIVE_DATA || '/data/wardrive');
	},
	get python(): string {
		return env.WARDRIVE_PYTHON || 'python3';
	},
	get reviewTool(): string {
		return env.WARDRIVE_REVIEW_TOOL || '/app/tools/wardrive_review.py';
	},
	/** Host names the app answers to, e.g. wardrive.russellland.dev. */
	get allowedHosts(): string[] {
		return (env.ANALYZER_ALLOWED_HOSTS || '')
			.split(',')
			.map((h) => h.trim().toLowerCase())
			.filter(Boolean);
	},
	/**
	 * The only peer address allowed to reach the UI and API: nginx, which has already run the
	 * oauth2-proxy check. Empty disables the check (running outside the stack).
	 */
	get trustedProxy(): string {
		return (env.ANALYZER_TRUSTED_PROXY || '').trim();
	},
	/** Shared with the api container, which calls /api/admin/ingest after each upload. */
	get adminToken(): string {
		return env.ADMIN_API_TOKEN || '';
	},
	/** Backstop scan of the session directory, in case an ingest notification was missed. */
	get scanMinutes(): number {
		return Math.max(1, Number(env.WARDRIVE_SCAN_MINUTES) || 15);
	},
	get postgres() {
		return {
			host: env.POSTGRES_HOST || 'postgres',
			port: Number(env.POSTGRES_PORT) || 5432,
			database: env.POSTGRES_DB || 'wardrive',
			user: env.POSTGRES_USER || 'wardrive',
			password: env.POSTGRES_PASSWORD || ''
		};
	}
};

export const DEMO_DIR = '_demo';
/** Same rule as the home API: validated, never sanitised. */
export const SESSION_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/;

export function log(event: string, fields: Record<string, unknown> = {}) {
	// One JSON line per event, like the api container, so `docker compose logs` reads the same.
	console.log(JSON.stringify({ t: new Date().toISOString(), event, ...fields }));
}
