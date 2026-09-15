import { timingSafeEqual } from 'node:crypto';
import { config } from './config';

/**
 * Guard for /api/admin/* - the routes the api.russellland.dev service calls over the docker
 * network, the same pattern as dogsitwithruss.com.
 *
 * Two independent things keep these routes off the internet:
 *
 *   1. nginx returns 404 for /api/admin/ on the public vhost, so a request from outside never
 *      reaches this app at all.
 *   2. This check. It covers what nginx cannot: another container on the global-nginx network
 *      reaching rustyserver-wardrive:3000 directly.
 *
 * X-Admin-Actor is written to the log but is caller-supplied and never treated as a credential.
 */

export type AdminCheck = { ok: true; actor: string } | { ok: false; status: number; error: string };

export function checkAdminAuth(request: Request): AdminCheck {
	const expected = config.adminToken;

	// Fail closed. An unset token must not mean "no check required".
	if (expected.length < 32) {
		console.error('ADMIN_API_TOKEN is unset or shorter than 32 characters; refusing admin call');
		return { ok: false, status: 503, error: 'admin endpoint is not configured' };
	}

	const presented = Buffer.from(request.headers.get('x-admin-token') ?? '');
	const wanted = Buffer.from(expected);
	if (presented.length !== wanted.length || !timingSafeEqual(presented, wanted)) {
		return { ok: false, status: 403, error: 'forbidden' };
	}

	return { ok: true, actor: (request.headers.get('x-admin-actor') ?? 'unknown').slice(0, 200) };
}
