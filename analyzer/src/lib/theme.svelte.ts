import { browser } from '$app/environment';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

const STORAGE_KEY = 'theme';

function readStoredPreference(): ThemePreference {
	if (!browser) return 'system';
	try {
		const stored = localStorage.getItem(STORAGE_KEY);
		return stored === 'light' || stored === 'dark' ? stored : 'system';
	} catch {
		return 'system';
	}
}

function systemTheme(): ResolvedTheme {
	if (!browser) return 'dark';
	return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

class ThemeStore {
	preference = $state<ThemePreference>('system');
	systemPrefersDark = $state<boolean>(false);

	/** The theme actually rendered right now. */
	get resolved(): ResolvedTheme {
		if (this.preference === 'system') {
			return this.systemPrefersDark ? 'dark' : 'light';
		}
		return this.preference;
	}

	/**
	 * Hydrate from localStorage and start tracking the OS setting. Safe to call
	 * more than once. Returns a teardown function for the media query listener.
	 */
	init(): () => void {
		if (!browser) return () => {};

		this.preference = readStoredPreference();

		const query = window.matchMedia('(prefers-color-scheme: dark)');
		this.systemPrefersDark = query.matches;

		const onChange = (event: MediaQueryListEvent) => {
			this.systemPrefersDark = event.matches;
		};
		query.addEventListener('change', onChange);

		return () => query.removeEventListener('change', onChange);
	}

	set(preference: ThemePreference) {
		this.preference = preference;
		if (!browser) return;
		try {
			if (preference === 'system') {
				localStorage.removeItem(STORAGE_KEY);
			} else {
				localStorage.setItem(STORAGE_KEY, preference);
			}
		} catch {
			// Storage can be unavailable (private mode, blocked cookies). The
			// in-memory preference still applies for this session.
		}
	}

	/** Applies the resolved theme to <html>. Call from an $effect. */
	apply() {
		if (!browser) return;
		document.documentElement.setAttribute('data-theme', this.resolved);
	}
}

export const theme = new ThemeStore();
