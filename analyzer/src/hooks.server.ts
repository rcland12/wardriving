import { error, type Handle, type ServerInit } from '@sveltejs/kit';
import { config } from '$lib/server/config';
import { startIngestion } from '$lib/server/ingest';

export const init: ServerInit = () => {
	// Catch up on anything uploaded while this container was down, then keep a slow backstop
	// scan running; the api's post-upload notification is what makes new sessions appear.
	startIngestion();
};

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

function peer(address: string): string {
	return address.replace(/^::ffff:/, '');
}

export const handle: Handle = async ({ event, resolve }) => {
	const path = event.url.pathname;
	event.locals.user = null;

	// The container healthcheck, and the api's admin calls (which carry their own token and are
	// 404'd by nginx on the public vhost).
	if (path === '/api/health' || path.startsWith('/api/admin/')) return resolve(event);

	// Everything else must come through nginx, after oauth2-proxy has admitted the user. Pinning
	// the peer address means another container on global-nginx can't skip the login by talking
	// to this one directly - the same idea as oauth2-proxy's TRUSTED_PROXY_IPS pin.
	if (config.trustedProxy && peer(event.getClientAddress()) !== config.trustedProxy) {
		error(403, 'direct access is not allowed');
	}

	// Refuse any other Host: a page that points its own domain here (DNS rebinding) sends its own.
	const host = (event.request.headers.get('host') ?? '').toLowerCase().replace(/:\d+$/, '');
	if (!LOCAL_HOSTS.has(host) && !config.allowedHosts.includes(host)) {
		error(403, `host ${host || '(none)'} is not allowed`);
	}

	// Set by nginx from oauth2-proxy's X-Auth-Request-Email; used to attribute fixes and uploads.
	// Trusted only because of the peer check above.
	if (config.trustedProxy) {
		event.locals.user =
			(event.request.headers.get('x-email') || event.request.headers.get('x-user') || '').slice(
				0,
				200
			) || null;
	}
	return resolve(event);
};
