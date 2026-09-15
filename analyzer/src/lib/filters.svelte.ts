import { SECURITY_ORDER } from './security';
import type { Band, DeviceSummary, Phy, SessionMeta, Security } from './types';

/**
 * Filter state shared by the map and stats pages. It round-trips through the URL, so any view
 * can be bookmarked or opened in another tab, and only non-default values are written.
 */

export const BANDS: Band[] = ['2.4', '5', '6', 'bt', ''];
export type TriState = 'any' | 'yes' | 'no';
export type Bounds = [west: number, south: number, east: number, north: number];

class Filters {
	q = $state('');
	regex = $state(false);
	phy = $state<Phy[]>(['wifi', 'bt']);
	sec = $state<Security[]>([...SECURITY_ORDER]);
	bands = $state<Band[]>([...BANDS]);
	channel = $state('');
	/** dBm; -100 means no minimum. Devices with an unknown signal are dropped once it is raised. */
	minSignal = $state(-100);
	/** Session names; empty means every session. */
	sessions = $state<string[]>([]);
	since = $state('');
	until = $state('');
	wps = $state<TriState>('any');
	hidden = $state<TriState>('any');
	manuf = $state('');
	minSessions = $state(1);
	inView = $state(false);

	reset() {
		this.q = '';
		this.regex = false;
		this.phy = ['wifi', 'bt'];
		this.sec = [...SECURITY_ORDER];
		this.bands = [...BANDS];
		this.channel = '';
		this.minSignal = -100;
		this.sessions = [];
		this.since = '';
		this.until = '';
		this.wps = 'any';
		this.hidden = 'any';
		this.manuf = '';
		this.minSessions = 1;
		this.inView = false;
	}

	/** How many filters differ from their defaults. */
	get active(): number {
		return [
			this.q !== '',
			this.phy.length !== 2,
			this.sec.length !== SECURITY_ORDER.length,
			this.bands.length !== BANDS.length,
			this.channel !== '',
			this.minSignal > -100,
			this.sessions.length > 0,
			this.since !== '' || this.until !== '',
			this.wps !== 'any',
			this.hidden !== 'any',
			this.manuf !== '',
			this.minSessions > 1,
			this.inView
		].filter(Boolean).length;
	}

	toParams(): URLSearchParams {
		const p = new URLSearchParams();
		if (this.q) p.set('q', this.q);
		if (this.regex) p.set('re', '1');
		if (this.phy.length !== 2) p.set('phy', this.phy.join(','));
		if (this.sec.length !== SECURITY_ORDER.length) p.set('sec', this.sec.join(','));
		if (this.bands.length !== BANDS.length)
			p.set('band', this.bands.map((b) => b || 'none').join(','));
		if (this.channel) p.set('ch', this.channel);
		if (this.minSignal > -100) p.set('sig', String(this.minSignal));
		if (this.sessions.length) p.set('s', this.sessions.join(','));
		if (this.since) p.set('since', this.since);
		if (this.until) p.set('until', this.until);
		if (this.wps !== 'any') p.set('wps', this.wps);
		if (this.hidden !== 'any') p.set('hidden', this.hidden);
		if (this.manuf) p.set('manuf', this.manuf);
		if (this.minSessions > 1) p.set('seen', String(this.minSessions));
		if (this.inView) p.set('view', '1');
		return p;
	}

	load(p: URLSearchParams) {
		const list = (key: string) => (p.get(key) ?? '').split(',').filter((v) => v !== '');
		const tri = (key: string): TriState =>
			p.get(key) === 'yes' || p.get(key) === 'no' ? (p.get(key) as TriState) : 'any';
		this.q = p.get('q') ?? '';
		this.regex = p.get('re') === '1';
		this.phy = p.has('phy')
			? (list('phy').filter((v) => v === 'wifi' || v === 'bt') as Phy[])
			: ['wifi', 'bt'];
		this.sec = p.has('sec')
			? SECURITY_ORDER.filter((s) => list('sec').includes(s))
			: [...SECURITY_ORDER];
		this.bands = p.has('band')
			? BANDS.filter((b) => list('band').includes(b || 'none'))
			: [...BANDS];
		this.channel = p.get('ch') ?? '';
		this.minSignal = Math.max(-100, Math.min(0, Number(p.get('sig')) || -100));
		this.sessions = list('s');
		this.since = p.get('since') ?? '';
		this.until = p.get('until') ?? '';
		this.wps = tri('wps');
		this.hidden = tri('hidden');
		this.manuf = p.get('manuf') ?? '';
		this.minSessions = Math.max(1, Number(p.get('seen')) || 1);
		this.inView = p.get('view') === '1';
	}

	/** The search as a predicate, or an error message for an invalid regular expression. */
	matcher(): ((d: DeviceSummary) => boolean) | string {
		const q = this.q.trim();
		if (!q) return () => true;
		let test: (s: string) => boolean;
		if (this.regex) {
			try {
				const re = new RegExp(q, 'i');
				test = (s) => re.test(s);
			} catch (err) {
				return err instanceof Error ? err.message : 'invalid regular expression';
			}
		} else {
			const needle = q.toLowerCase();
			test = (s) => s.toLowerCase().includes(needle);
		}
		return (d) => test(d.name) || test(d.mac) || test(d.manuf) || test(d.crypt);
	}
}

export const filters = new Filters();

function dayStart(date: string): number {
	return new Date(`${date}T00:00:00`).getTime() / 1000;
}

export interface FilterResult {
	devices: DeviceSummary[];
	error: string | null;
}

/** Everything except the map-view bounds, which only the map page applies. */
export function applyFilters(
	devices: DeviceSummary[],
	sessions: SessionMeta[],
	bounds: Bounds | null = null
): FilterResult {
	const f = filters;
	const match = f.matcher();
	if (typeof match === 'string') return { devices: [], error: match };
	const phy = new Set(f.phy);
	const sec = new Set(f.sec);
	const bands = new Set(f.bands);
	const wanted = f.sessions.length
		? new Set(sessions.flatMap((s, i) => (f.sessions.includes(s.name) ? [i] : [])))
		: null;
	const since = f.since ? dayStart(f.since) : 0;
	const until = f.until ? dayStart(f.until) + 86400 : Infinity;
	const view = f.inView ? bounds : null;

	const out = devices.filter((d) => {
		if (!phy.has(d.phy) || !sec.has(d.sec) || !bands.has(d.band)) return false;
		if (f.channel && d.channel !== f.channel) return false;
		if (f.minSignal > -100 && (d.signal === 0 || d.signal < f.minSignal)) return false;
		if (wanted && !d.sessions.some((i) => wanted.has(i))) return false;
		if (d.last < since || d.first > until) return false;
		if (f.wps !== 'any' && d.wps !== (f.wps === 'yes')) return false;
		if (f.hidden !== 'any' && (d.phy !== 'wifi' || d.hidden !== (f.hidden === 'yes'))) return false;
		if (f.manuf && d.manuf !== f.manuf) return false;
		if (d.sessions.length < f.minSessions) return false;
		if (view && !(d.lon >= view[0] && d.lat >= view[1] && d.lon <= view[2] && d.lat <= view[3])) {
			return false;
		}
		return match(d);
	});
	return { devices: out, error: null };
}
