<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import BarList, { type BarRow } from '$lib/components/BarList.svelte';
	import ColumnChart, { type Column } from '$lib/components/ColumnChart.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { loadDevices } from '$lib/data';
	import { applyFilters, filters } from '$lib/filters.svelte';
	import { fmtDate, fmtNum, fmtPct, sessionLabel } from '$lib/format';
	import { SECURITY, SECURITY_ORDER } from '$lib/security';
	import type { DevicesResponse } from '$lib/types';

	const demo = $derived(page.url.searchParams.get('demo') === '1');
	let data = $state<DevicesResponse | null>(null);
	let loadError = $state<string | null>(null);

	$effect(() => {
		filters.load(page.url.searchParams);
	});

	$effect(() => {
		const isDemo = demo;
		data = null;
		loadDevices(isDemo)
			.then((d) => (data = d))
			.catch((err) => (loadError = err instanceof Error ? err.message : String(err)));
	});

	const devices = $derived(data ? applyFilters(data.devices, data.sessions).devices : []);
	const wifi = $derived(devices.filter((d) => d.phy === 'wifi'));

	const tiles = $derived([
		{ label: 'Devices', value: fmtNum(devices.length) },
		{ label: 'Wi-Fi networks', value: fmtNum(wifi.length) },
		{
			label: 'Open Wi-Fi',
			value: fmtNum(wifi.filter((d) => d.sec === 'OPEN').length),
			sub: fmtPct(wifi.filter((d) => d.sec === 'OPEN').length, wifi.length)
		},
		{
			label: 'Hidden SSIDs',
			value: fmtNum(wifi.filter((d) => d.hidden).length),
			sub: fmtPct(wifi.filter((d) => d.hidden).length, wifi.length)
		},
		{
			label: 'WPS enabled',
			value: fmtNum(wifi.filter((d) => d.wps).length),
			sub: fmtPct(wifi.filter((d) => d.wps).length, wifi.length)
		},
		{ label: 'Bluetooth', value: fmtNum(devices.length - wifi.length) }
	]);

	const securityRows = $derived<BarRow[]>(
		SECURITY_ORDER.map((sec) => ({
			key: sec,
			label: SECURITY[sec].label,
			value: devices.filter((d) => d.sec === sec).length,
			// One series, one color: the shape marker beside each label carries identity.
			sec
		})).filter((r) => r.value > 0)
	);

	function topBy(
		list: typeof devices,
		key: (d: (typeof devices)[number]) => string,
		n: number
	): BarRow[] {
		const counts = new Map<string, number>();
		for (const d of list) {
			const k = key(d);
			if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
		}
		return [...counts.entries()]
			.sort((a, b) => b[1] - a[1])
			.slice(0, n)
			.map(([k, v]) => ({ key: k, label: k, value: v }));
	}

	const manufRows = $derived(topBy(devices, (d) => (d.manuf === 'Unknown' ? '' : d.manuf), 12));
	const ssidRows = $derived(topBy(wifi, (d) => d.name, 12));

	function channelColumns(band: '2.4' | '5'): Column[] {
		const counts = new Map<number, number>();
		for (const d of wifi)
			if (d.band === band) counts.set(Number(d.channel), (counts.get(Number(d.channel)) ?? 0) + 1);
		const keys =
			band === '2.4'
				? Array.from({ length: 13 }, (_, i) => i + 1)
				: [...counts.keys()].filter((c) => c > 14).sort((a, b) => a - b);
		return keys.map((ch) => ({
			key: String(ch),
			label: String(ch),
			values: [counts.get(ch) ?? 0],
			title: `Channel ${ch}`
		}));
	}
	const ch24 = $derived(channelColumns('2.4'));
	const ch5 = $derived(channelColumns('5'));

	const signalColumns = $derived.by<Column[]>(() => {
		const bins = new Map<number, number>();
		for (const d of devices)
			if (d.signal)
				bins.set(Math.floor(d.signal / 5) * 5, (bins.get(Math.floor(d.signal / 5) * 5) ?? 0) + 1);
		const out: Column[] = [];
		for (let b = -100; b <= -20; b += 5) {
			out.push({
				key: String(b),
				label: String(b),
				values: [bins.get(b) ?? 0],
				title: `${b} to ${b + 4} dBm`
			});
		}
		return out;
	});

	// New = first time this device appeared in any session; the rest were already known.
	const sessionColumns = $derived.by<Column[]>(() => {
		if (!data) return [];
		const fresh = data.sessions.map(() => 0);
		const seen = data.sessions.map(() => 0);
		for (const d of devices) {
			const first = Math.min(...d.sessions);
			for (const i of d.sessions) {
				if (i === first) fresh[i]++;
				else seen[i]++;
			}
		}
		return data.sessions.map((s, i) => ({
			key: s.name,
			label: fmtDate(s.start).replace(/, \d{4}$/, ''),
			values: [fresh[i], seen[i]],
			title: sessionLabel(s.name, s.start)
		}));
	});

	function toMap(params: Record<string, string>) {
		const p = filters.toParams();
		for (const [k, v] of Object.entries(params)) p.set(k, v);
		if (demo) p.set('demo', '1');
		goto(`/?${p}`);
	}

	const mapHref = $derived.by(() => {
		const p = filters.toParams();
		if (demo) p.set('demo', '1');
		const q = p.toString();
		return q ? `/?${q}` : '/';
	});
</script>

<svelte:head>
	<title>Stats · Wardrive Analyzer</title>
</svelte:head>

<div class="page">
	<div class="head">
		<div>
			<h1>Stats</h1>
			<p class="muted">
				{#if filters.active}
					Showing the {fmtNum(devices.length)} devices that match {filters.active} filter{filters.active ===
					1
						? ''
						: 's'}.
					<a href={mapHref}>Edit filters on the map</a> ·
					<a href="/stats{demo ? '?demo=1' : ''}">Clear</a>
				{:else}
					Every device across all sessions. Filters set on the map apply here too. Click a bar to
					see those devices on the map.
				{/if}
			</p>
		</div>
	</div>

	{#if loadError}
		<p class="error card"><Icon name="alert" size="16px" /> {loadError}</p>
	{:else if !data}
		<p class="muted">Loading...</p>
	{:else}
		<div class="tiles">
			{#each tiles as t (t.label)}
				<div class="tile">
					<span class="label">{t.label}</span>
					<span class="value"
						>{t.value}{#if t.sub}<span class="sub"> {t.sub}</span>{/if}</span
					>
				</div>
			{/each}
		</div>

		<div class="grid">
			<section class="card">
				<h2>Security</h2>
				<p class="desc">What protects each network. Bluetooth is listed for completeness.</p>
				<BarList rows={securityRows} total={devices.length} onpick={(r) => toMap({ sec: r.key })} />
			</section>

			<section class="card">
				<h2>Top manufacturers</h2>
				<p class="desc">
					From the MAC address prefix (the OUI), so routers from ISPs show under their maker.
				</p>
				<BarList rows={manufRows} total={devices.length} onpick={(r) => toMap({ manuf: r.key })} />
			</section>

			<section class="card wide">
				<h2>Devices per session</h2>
				<p class="desc">
					New devices appeared for the first time in that session; the rest had been seen on an
					earlier drive.
				</p>
				<ColumnChart
					columns={sessionColumns}
					series={[
						{ label: 'New', color: 'var(--series-1)' },
						{ label: 'Seen before', color: 'var(--series-muted)' }
					]}
					ariaLabel="Devices per session, split into new and previously seen"
					labelEvery={Math.max(1, Math.ceil(sessionColumns.length / 12))}
				/>
			</section>

			<section class="card">
				<h2>2.4 GHz channels</h2>
				<p class="desc">
					Wi-Fi networks per channel. 1, 6 and 11 don't overlap, so most routers use them.
				</p>
				<ColumnChart
					columns={ch24}
					series={[{ label: 'Networks', color: 'var(--series-1)' }]}
					ariaLabel="Networks per 2.4 GHz channel"
					height={180}
				/>
			</section>

			<section class="card">
				<h2>5 GHz channels</h2>
				<p class="desc">Wi-Fi networks per channel.</p>
				{#if ch5.length}
					<ColumnChart
						columns={ch5}
						series={[{ label: 'Networks', color: 'var(--series-1)' }]}
						ariaLabel="Networks per 5 GHz channel"
						height={180}
						labelEvery={ch5.length > 16 ? 2 : 1}
					/>
				{:else}
					<p class="muted">No 5 GHz networks in this selection.</p>
				{/if}
			</section>

			<section class="card">
				<h2>Strongest signal</h2>
				<p class="desc">
					Each device's best reading, in 5 dBm steps. Around -50 dBm and up means you were close.
				</p>
				<ColumnChart
					columns={signalColumns}
					series={[{ label: 'Devices', color: 'var(--series-1)' }]}
					ariaLabel="Distribution of strongest signal"
					height={180}
					labelEvery={2}
				/>
			</section>

			<section class="card">
				<h2>Most common network names</h2>
				<p class="desc">
					SSIDs broadcast by more than one access point are usually ISP hotspots or chains.
				</p>
				{#if ssidRows.length}
					<BarList rows={ssidRows} total={wifi.length} onpick={(r) => toMap({ q: r.key })} />
				{:else}
					<p class="muted">No named networks in this selection.</p>
				{/if}
			</section>
		</div>
	{/if}
</div>

<style>
	.page {
		height: 100%;
		overflow-y: auto;
		padding: 20px clamp(16px, 3vw, 32px) 48px;
	}

	.page > * {
		max-width: 1180px;
		margin-left: auto;
		margin-right: auto;
	}

	.head {
		margin-bottom: 16px;
	}

	h1 {
		margin: 0 0 2px;
		font-size: 22px;
	}

	.head p {
		margin: 0;
	}

	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
		gap: 10px;
		margin-bottom: 14px;
	}

	.tile {
		display: grid;
		gap: 2px;
		padding: 12px 14px;
		border: 1px solid var(--border);
		border-radius: var(--r-md);
		background: var(--surface);
	}

	.tile .label {
		color: var(--text-muted);
		font-size: 12.5px;
	}

	.tile .value {
		font-size: 22px;
		font-weight: 600;
	}

	.tile .sub {
		color: var(--text-faint);
		font-size: 14px;
		font-weight: 500;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 12px;
	}

	.grid .wide {
		grid-column: 1 / -1;
	}

	section {
		padding: 14px 16px 16px;
		min-width: 0;
	}

	h2 {
		margin: 0;
		font-size: 15px;
	}

	.desc {
		margin: 2px 0 12px;
		color: var(--text-muted);
		font-size: 12.5px;
	}

	.error {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 10px 14px;
		color: var(--danger);
	}

	@media (max-width: 860px) {
		.grid {
			grid-template-columns: 1fr;
		}
	}
</style>
