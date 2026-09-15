/** Display formatting. Capture times are epoch seconds (UTC); everything shows in local time. */

const dateFmt = new Intl.DateTimeFormat(undefined, {
	month: 'short',
	day: 'numeric',
	year: 'numeric'
});
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
	month: 'short',
	day: 'numeric',
	hour: 'numeric',
	minute: '2-digit',
	second: '2-digit'
});
const numFmt = new Intl.NumberFormat();
const compactFmt = new Intl.NumberFormat(undefined, {
	notation: 'compact',
	maximumFractionDigits: 1
});

export function fmtDate(ts: number): string {
	return ts ? dateFmt.format(ts * 1000) : '-';
}

export function fmtTime(ts: number): string {
	return ts ? timeFmt.format(ts * 1000) : '-';
}

export function fmtDateTime(ts: number): string {
	return ts ? dateTimeFmt.format(ts * 1000) : '-';
}

export function fmtNum(n: number): string {
	return numFmt.format(n);
}

export function fmtCompact(n: number): string {
	return compactFmt.format(n);
}

export function fmtPct(part: number, whole: number): string {
	if (!whole) return '0%';
	const pct = (100 * part) / whole;
	return pct > 0 && pct < 1 ? '<1%' : `${Math.round(pct)}%`;
}

export function fmtDuration(seconds: number): string {
	const s = Math.max(0, Math.round(seconds));
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
	if (m) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
	return `${s}s`;
}

/** Miles, matching the Pi's default units = "imperial". */
export function fmtDistance(metres: number): string {
	const miles = metres / 1609.344;
	return miles < 0.1 ? `${Math.round(metres * 3.28084)} ft` : `${miles.toFixed(1)} mi`;
}

export function fmtBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
	return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function fmtSignal(dbm: number): string {
	return dbm ? `${dbm} dBm` : '-';
}

/** Session names are wardrive-YYYYMMDD-HH-MM-SS-N in UTC; shown as a local date and time. */
export function sessionLabel(name: string, start = 0): string {
	const m = /(\d{4})(\d{2})(\d{2})-(\d{2})-(\d{2})-(\d{2})/.exec(name);
	const ts = start || (m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000 : 0);
	return ts ? `${fmtDate(ts)}, ${fmtTime(ts)}` : name;
}
