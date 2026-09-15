<script lang="ts">
	import { loadDevice } from '$lib/data';
	import { fmtDateTime, fmtNum, fmtSignal, sessionLabel } from '$lib/format';
	import { authMethods, BAND_LABEL, SECURITY } from '$lib/security';
	import type { DeviceDetail, DeviceSummary, SessionMeta, Sighting } from '$lib/types';
	import Icon from './Icon.svelte';
	import SecurityMark from './SecurityMark.svelte';
	import SignalChart from './SignalChart.svelte';

	interface Props {
		device: DeviceSummary;
		sessions: SessionMeta[];
		demo: boolean;
		onclose: () => void;
		onzoom: (lat: number, lon: number) => void;
		/** Readings to draw on the map (empty when the toggle is off). */
		onsightings: (sightings: Sighting[]) => void;
	}

	let { device, sessions, demo, onclose, onzoom, onsightings }: Props = $props();

	let detail = $state<DeviceDetail | null>(null);
	let loadError = $state<string | null>(null);
	let chartSession = $state('');
	let showHeard = $state(true);
	let raw = $state<string | null>(null);
	let rawOpen = $state(false);
	let copied = $state(false);

	$effect(() => {
		const id = device.id;
		detail = null;
		loadError = null;
		raw = null;
		rawOpen = false;
		let cancelled = false;
		loadDevice(demo, id)
			.then((d) => {
				if (cancelled) return;
				detail = d;
				// Chart the session with the most readings by default.
				const counts = new Map<string, number>();
				for (const s of d.sightings) counts.set(s.session, (counts.get(s.session) ?? 0) + 1);
				chartSession =
					[...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? d.records[0]?.session ?? '';
			})
			.catch((err) => {
				if (!cancelled) loadError = err instanceof Error ? err.message : String(err);
			});
		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		onsightings(showHeard && detail ? detail.sightings : []);
	});

	$effect(() => {
		// The raw record follows the session picked for the chart.
		void chartSession;
		raw = null;
		rawOpen = false;
	});

	// The strongest session's record carries the details shown up top.
	const best = $derived(
		detail?.records.reduce((a, b) =>
			b.signalMax && (!a.signalMax || b.signalMax > a.signalMax) ? b : a
		)
	);
	const weakest = $derived(
		detail?.records.reduce(
			(min, r) => (r.signalMin && (!min || r.signalMin < min) ? r.signalMin : min),
			0
		) ?? 0
	);
	const wps = $derived(detail?.records.find((r) => r.wpsName || r.wpsModel));
	const chartReadings = $derived(detail?.sightings.filter((s) => s.session === chartSession) ?? []);
	const methods = $derived(authMethods(device.crypt));
	const style = $derived(SECURITY[device.sec]);

	async function toggleRaw() {
		rawOpen = !rawOpen;
		if (!rawOpen || raw !== null || !detail) return;
		const session = chartSession || detail.records[0].session;
		const [phy, ...mac] = device.id.split(':');
		const res = await fetch(
			`/api/devices/${phy}/${mac.join(':')}/raw?session=${encodeURIComponent(session)}${demo ? '&demo=1' : ''}`
		);
		raw = res.ok ? JSON.stringify(await res.json(), null, 2) : `HTTP ${res.status}`;
	}

	async function copyMac() {
		try {
			await navigator.clipboard.writeText(device.mac);
			copied = true;
			setTimeout(() => (copied = false), 1200);
		} catch {
			// Clipboard needs a secure context; localhost qualifies, other hosts may not.
		}
	}

	const sessionByName = $derived(new Map(sessions.map((s) => [s.name, s])));
</script>

<aside class="detail" aria-label="Device details">
	<header>
		<div class="title">
			<p class="eyebrow">
				<Icon name={device.phy === 'bt' ? 'bluetooth' : 'wifi'} size="12px" />
				{device.type || (device.phy === 'bt' ? 'Bluetooth' : 'Wi-Fi')}
			</p>
			<h2>
				{#if device.name}{device.name}{:else}<em
						>{device.phy === 'wifi' ? 'Hidden network' : 'Unnamed device'}</em
					>{/if}
			</h2>
			<div class="mac">
				<code>{device.mac}</code>
				<button
					class="btn ghost small icon"
					type="button"
					onclick={copyMac}
					aria-label="Copy MAC address"
				>
					<Icon name={copied ? 'check' : 'copy'} size="13px" />
				</button>
			</div>
		</div>
		<button class="btn ghost icon" type="button" onclick={onclose} aria-label="Close details">
			<Icon name="x" size="16px" />
		</button>
	</header>

	<div class="badges">
		<span class="badge"><SecurityMark sec={device.sec} size={12} /> {style.label}</span>
		{#if device.wps}<span class="badge warn">WPS</span>{/if}
		{#if device.hidden && device.phy === 'wifi'}<span class="badge">Hidden SSID</span>{/if}
		{#if device.sessions.length > 1}<span class="badge accent"
				>Seen in {device.sessions.length} sessions</span
			>{/if}
	</div>

	<div class="actions">
		{#if device.lat || device.lon}
			<button class="btn small" type="button" onclick={() => onzoom(device.lat, device.lon)}>
				<Icon name="crosshair" size="14px" /> Zoom to
			</button>
			<label class="btn small toggle" class:on={showHeard}>
				<input type="checkbox" class="sr-only" bind:checked={showHeard} />
				<Icon name="eye" size="14px" /> Where heard
			</label>
			<a
				class="btn small"
				href="https://www.openstreetmap.org/?mlat={device.lat}&mlon={device.lon}#map=19/{device.lat}/{device.lon}"
				target="_blank"
				rel="noreferrer"
			>
				<Icon name="external" size="14px" /> OSM
			</a>
		{/if}
	</div>

	{#if loadError}
		<p class="error" role="alert"><Icon name="alert" size="14px" /> {loadError}</p>
	{/if}

	<dl class="facts">
		{#if device.phy === 'wifi'}
			<div>
				<dt>Encryption</dt>
				<dd>{device.crypt || 'not captured'}</dd>
			</div>
			{#if methods.length}<div>
					<dt>Login</dt>
					<dd>{methods.join(', ')}</dd>
				</div>{/if}
			{#if best?.mfp}<div>
					<dt>Protected frames</dt>
					<dd>{best.mfp}</dd>
				</div>{/if}
			{#if wps}<div>
					<dt>WPS device</dt>
					<dd>{[wps.wpsName, wps.wpsModel].filter(Boolean).join(' · ')}</dd>
				</div>{/if}
			<div>
				<dt>Band</dt>
				<dd>
					{BAND_LABEL[device.band]}{best?.frequency
						? ` · ${(best.frequency / 1000).toFixed(0)} MHz`
						: ''}
				</dd>
			</div>
			<div>
				<dt>Channel</dt>
				<dd>{device.channel || '-'}{best?.htMode ? ` · ${best.htMode}` : ''}</dd>
			</div>
			{#if best?.maxRate}<div>
					<dt>Max rate</dt>
					<dd>{best.maxRate} Mbps</dd>
				</div>{/if}
			{#if best?.country}<div>
					<dt>Country</dt>
					<dd>{best.country}</dd>
				</div>{/if}
		{/if}
		<div>
			<dt>Manufacturer</dt>
			<dd>{device.manuf || 'Unknown'}</dd>
		</div>
		<div>
			<dt>Signal</dt>
			<dd class="num">
				{fmtSignal(device.signal)}{weakest ? ` strongest · ${weakest} dBm weakest` : ''}
			</dd>
		</div>
		{#if device.lat || device.lon}
			<div>
				<dt>Location</dt>
				<dd class="num">
					{device.lat.toFixed(6)}, {device.lon.toFixed(6)}
					<span class="faint">{device.phy === 'wifi' ? '(where signal peaked)' : '(average)'}</span>
				</dd>
			</div>
		{/if}
		{#if best?.alt}<div>
				<dt>Altitude</dt>
				<dd class="num">{Math.round(best.alt * 3.28084)} ft</dd>
			</div>{/if}
		<div>
			<dt>First seen</dt>
			<dd class="num">{fmtDateTime(device.first)}</dd>
		</div>
		<div>
			<dt>Last seen</dt>
			<dd class="num">{fmtDateTime(device.last)}</dd>
		</div>
		<div>
			<dt>Packets</dt>
			<dd class="num">{fmtNum(device.packets)}</dd>
		</div>
		{#if best?.uuids.length}<div>
				<dt>Services</dt>
				<dd class="mono">{best.uuids.join(', ')}</dd>
			</div>{/if}
	</dl>

	{#if detail}
		<section>
			<h3>Signal over time</h3>
			{#if detail.records.length > 1}
				<label class="sr-only" for="chart-session">Session</label>
				<select id="chart-session" bind:value={chartSession}>
					{#each detail.records as r (r.session)}
						<option value={r.session}>
							{sessionLabel(r.session, sessionByName.get(r.session)?.start)} ({detail.sightings.filter(
								(s) => s.session === r.session
							).length} readings)
						</option>
					{/each}
				</select>
			{/if}
			<SignalChart sightings={chartReadings} />
		</section>

		<section>
			<h3>Sessions</h3>
			<table class="data">
				<thead>
					<tr><th>Session</th><th class="num">Best</th><th class="num">Packets</th></tr>
				</thead>
				<tbody>
					{#each detail.records as r (r.session)}
						<tr>
							<td>{sessionLabel(r.session, sessionByName.get(r.session)?.start)}</td>
							<td class="num">{r.signalMax || '-'}</td>
							<td class="num">{fmtNum(r.packets)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>

		<section>
			<button class="disclosure" type="button" aria-expanded={rawOpen} onclick={toggleRaw}>
				<Icon name={rawOpen ? 'chevron-down' : 'chevron-right'} size="14px" /> Raw Kismet record
			</button>
			{#if rawOpen}
				<pre>{raw ?? 'Loading...'}</pre>
			{/if}
		</section>
	{:else if !loadError}
		<p class="muted loading">Loading details...</p>
	{/if}
</aside>

<style>
	.detail {
		display: grid;
		align-content: start;
		gap: 12px;
		padding: 14px 16px 24px;
	}

	header {
		display: flex;
		align-items: flex-start;
		gap: 8px;
	}

	.title {
		flex: 1;
		min-width: 0;
	}

	.eyebrow {
		display: flex;
		align-items: center;
		gap: 5px;
	}

	h2 {
		margin: 0;
		font-size: 18px;
		font-weight: 600;
		line-height: 1.25;
		overflow-wrap: anywhere;
	}

	h2 em {
		color: var(--text-muted);
		font-weight: 500;
	}

	.mac {
		display: flex;
		align-items: center;
		gap: 2px;
		color: var(--text-muted);
	}

	.badges,
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.toggle.on {
		border-color: var(--accent);
		background: var(--accent-soft);
		color: var(--accent-text);
	}

	.toggle:focus-within {
		outline: 2px solid var(--focus);
		outline-offset: 1px;
	}

	.facts {
		display: grid;
		gap: 0;
		margin: 0;
		border-top: 1px solid var(--border);
	}

	.facts div {
		display: grid;
		grid-template-columns: 112px 1fr;
		gap: 10px;
		padding: 6px 0;
		border-bottom: 1px solid var(--border);
	}

	dt {
		color: var(--text-muted);
		font-size: 12.5px;
	}

	dd {
		margin: 0;
		overflow-wrap: anywhere;
	}

	section {
		display: grid;
		gap: 8px;
	}

	h3 {
		margin: 4px 0 0;
		font-size: 13px;
		font-weight: 600;
	}

	.disclosure {
		display: flex;
		align-items: center;
		gap: 4px;
		padding: 0;
		border: 0;
		background: none;
		color: var(--text);
		font: inherit;
		font-weight: 600;
		font-size: 13px;
		cursor: pointer;
	}

	pre {
		max-height: 360px;
		margin: 0;
		padding: 10px;
		overflow: auto;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface-inset);
		font-family: var(--font-mono);
		font-size: 11.5px;
	}

	.error {
		display: flex;
		gap: 6px;
		align-items: center;
		margin: 0;
		color: var(--danger);
	}

	.loading {
		margin: 0;
	}
</style>
