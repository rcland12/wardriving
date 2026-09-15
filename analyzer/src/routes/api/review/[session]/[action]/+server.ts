import { error, json, type RequestHandler } from '@sveltejs/kit';
import { scopeOf } from '$lib/server/library';
import { review, type ReviewAction } from '$lib/server/review';

const ACTIONS: ReviewAction[] = ['check', 'fix', 'dry-run', 'upload'];

// POST with a JSON body only: a cross-site form cannot send application/json without a CORS
// preflight, which this server never grants.
export const POST: RequestHandler = async ({ params, url, request, locals }) => {
	const action = params.action as ReviewAction;
	if (!ACTIONS.includes(action)) error(404, 'unknown action');
	if (!request.headers.get('content-type')?.startsWith('application/json')) {
		error(415, 'send Content-Type: application/json');
	}
	const body = await request.json().catch(() => ({}));
	if (action === 'upload' && body?.confirm !== params.session) {
		error(400, 'upload requires {"confirm": "<session name>"}');
	}
	return json(await review(scopeOf(url), params.session ?? '', action, locals.user));
};
