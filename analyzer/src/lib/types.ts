/** Types shared by the server routes and the browser. */

/** Security label, matching the Pi UI's classify_crypt() plus UNKNOWN for Wi-Fi with no crypt record. */
export type Security = 'OPEN' | 'WEP' | 'WPA' | 'WPA2' | 'WPA3' | 'UNKNOWN' | 'BT';
export type Phy = 'wifi' | 'bt';
export type Band = '2.4' | '5' | '6' | 'bt' | '';

export interface SessionMeta {
	name: string;
	demo: boolean;
	/** Epoch seconds from the capture itself. Wrong if the Pi's clock was not set yet. */
	start: number;
	end: number;
	wifi: number;
	bt: number;
	open: number;
	hidden: number;
	located: number;
	packets: number;
	/** Metres driven, from the GPS track. */
	distance: number;
	/** Unique devices first seen in this session (not in any earlier session). */
	newDevices: number;
	kismetBytes: number;
	uploadedAt: number;
	review: ReviewRecord;
}

/** The session's review and WiGLE upload, from the reviews and wigle_uploads tables. */
export interface ReviewRecord {
	reviewed?: { at: string; by?: string; fixes: string[]; rows: number; sha256: string };
	wigle?: {
		at: string;
		by?: string;
		transids: string[];
		rows: number;
		reviewed: boolean;
		donate: boolean;
		sha256: string;
	};
}

/** One device merged across every session that saw it. Short keys: thousands are sent at once. */
export interface DeviceSummary {
	/** `${phy}:${mac}`, unique. */
	id: string;
	mac: string;
	phy: Phy;
	/** Kismet's type label, e.g. "Wi-Fi AP", "BTLE". */
	type: string;
	/** SSID or Bluetooth name; "" when hidden or unnamed. */
	name: string;
	hidden: boolean;
	sec: Security;
	crypt: string;
	wps: boolean;
	manuf: string;
	channel: string;
	band: Band;
	/** Strongest signal in dBm; 0 when unknown (most Bluetooth). */
	signal: number;
	lat: number;
	lon: number;
	first: number;
	last: number;
	packets: number;
	/** Indexes into the sessions array sent alongside. */
	sessions: number[];
}

export interface DevicesResponse {
	sessions: SessionMeta[];
	devices: DeviceSummary[];
}

/** Everything one session recorded about a device. */
export interface DeviceRecord {
	session: string;
	type: string;
	name: string;
	hidden: boolean;
	crypt: string;
	sec: Security;
	wps: boolean;
	wpsName: string;
	wpsModel: string;
	manuf: string;
	channel: string;
	frequency: number;
	band: Band;
	htMode: string;
	maxRate: number;
	country: string;
	mfp: string;
	signalMax: number;
	signalMin: number;
	lat: number;
	lon: number;
	alt: number;
	avgLat: number;
	avgLon: number;
	first: number;
	last: number;
	packets: number;
	/** Bluetooth service UUIDs, when advertised. */
	uuids: string[];
}

export interface Sighting {
	session: string;
	ts: number;
	lat: number;
	lon: number;
	signal: number;
}

export interface DeviceDetail {
	id: string;
	mac: string;
	phy: Phy;
	records: DeviceRecord[];
	sightings: Sighting[];
}

export interface TrackPoint {
	ts: number;
	lat: number;
	lon: number;
	speed: number;
}

export interface TracksResponse {
	tracks: { session: string; points: [number, number][] }[];
}

/** Output of `wardrive_review.py --json check|fix|wigle`. */
export interface ReviewReport {
	verdict: 'OK' | 'WARN' | 'FAIL';
	info: string[];
	failures: string[];
	warnings: string[];
}

export interface ReviewResult {
	ok: boolean;
	error?: string;
	session?: string;
	report?: ReviewReport;
	fixes?: string[];
	rows?: number;
	sent?: boolean;
	dry_run?: boolean;
	blocked?: 'demo' | 'failures' | 'uploaded' | 'rejected';
	message?: string;
	file?: string;
	bytes?: number;
	reviewed?: boolean;
	donate?: boolean;
	transids?: string[];
	warning?: string;
}

export interface WigleTransaction {
	transid: string;
	fileName: string;
	status: string;
	percentDone: number;
	discoveredGps: number;
	btDiscoveredGps: number;
	totalGps: number;
	uploadedDate?: string;
	[key: string]: unknown;
}

export interface WigleStatus {
	ok: boolean;
	error?: string;
	queue_depth?: number;
	transactions?: WigleTransaction[];
}
