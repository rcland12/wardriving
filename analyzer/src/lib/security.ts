import type { Band, Phy, Security } from './types';

/**
 * Security classes, colors and marker shapes. Colors and shapes match the Pi UI
 * (wardrive/theme.py, wardrive/maprender.py MARKER_SHAPES): chosen to stay distinct with
 * red-green and blue-yellow color vision deficiency, and never used alone, since every
 * class also has its own shape and a text label.
 */

export type Shape = 'triangle' | 'diamond' | 'circle' | 'square' | 'plus' | 'ring';

export interface SecurityStyle {
	label: string;
	color: string;
	shape: Shape;
	description: string;
}

export const SECURITY: Record<Security, SecurityStyle> = {
	OPEN: {
		label: 'Open',
		color: '#ffdd00',
		shape: 'triangle',
		description: 'No encryption'
	},
	WEP: {
		label: 'WEP',
		color: '#dc5a1e',
		shape: 'diamond',
		description: 'Broken encryption'
	},
	WPA: {
		label: 'WPA',
		color: '#dc5a1e',
		shape: 'diamond',
		description: 'Original WPA (TKIP), weak'
	},
	WPA2: { label: 'WPA2', color: '#2878e6', shape: 'circle', description: 'WPA2' },
	WPA3: { label: 'WPA3', color: '#ebebeb', shape: 'square', description: 'WPA3 / SAE / OWE' },
	UNKNOWN: {
		label: 'Unknown',
		// Dark enough to stay apart from Bluetooth pink under deuteranopia (validated all-pairs).
		color: '#5b6470',
		shape: 'ring',
		description: 'No beacon with security details was captured'
	},
	BT: { label: 'Bluetooth', color: '#e66ebe', shape: 'plus', description: 'Bluetooth device' }
};

export const SECURITY_ORDER: Security[] = ['OPEN', 'WEP', 'WPA', 'WPA2', 'WPA3', 'UNKNOWN', 'BT'];

/** Collapse Kismet's crypt string ('WPA2 WPA2-PSK AES-CCMP') into one label, like the Pi does. */
export function classifyCrypt(raw: string, phy: Phy): Security {
	if (phy === 'bt') return 'BT';
	if (!raw) return 'UNKNOWN';
	if (raw.includes('WPA3') || raw.includes('OWE') || raw.includes('SAE')) return 'WPA3';
	if (raw.includes('WPA2')) return 'WPA2';
	if (raw.includes('WPA')) return 'WPA';
	if (raw.includes('WEP')) return 'WEP';
	return 'OPEN';
}

/** Key management from the crypt string: PSK (password), EAP (enterprise login), SAE, OWE. */
export function authMethods(raw: string): string[] {
	const out: string[] = [];
	if (/PSK/.test(raw)) out.push('PSK');
	if (/SAE/.test(raw)) out.push('SAE');
	if (/EAP/.test(raw)) out.push('Enterprise (EAP)');
	if (/OWE/.test(raw)) out.push('OWE');
	return out;
}

export function bandOf(frequencyKhz: number, phy: Phy): Band {
	if (phy === 'bt') return 'bt';
	const mhz = frequencyKhz / 1000;
	if (mhz >= 2400 && mhz < 2500) return '2.4';
	if (mhz >= 5150 && mhz < 5925) return '5';
	if (mhz >= 5925 && mhz <= 7125) return '6';
	return '';
}

export const BAND_LABEL: Record<Band, string> = {
	'2.4': '2.4 GHz',
	'5': '5 GHz',
	'6': '6 GHz',
	bt: 'Bluetooth',
	'': 'Unknown'
};
