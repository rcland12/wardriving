<script lang="ts">
	import { afterNavigate, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import DeviceDetail from '$lib/components/DeviceDetail.svelte';
	import DeviceTable from '$lib/components/DeviceTable.svelte';
	import FilterPanel from '$lib/components/FilterPanel.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import MapView, { type Basemap } from '$lib/components/MapView.svelte';
	import SecurityMark from '$lib/components/SecurityMark.svelte';
	import { loadDevices, loadTracks } from '$lib/data';
	import { applyFilters, filters, type Bounds } from '$lib/filters.svelte';
	import { fmtNum } from '$lib/format';
	import { SECURITY, SECURITY_ORDER } from '$lib/security';
	import { theme } from '$lib/theme.svelte';
	import type { DevicesResponse, Sighting, TracksResponse } from '$lib/types';

	const demo = $derived(page.url.searchParams.get('demo') === '1');

	let data = $state<DevicesResponse | null>(null);
	let tracks = $state<TracksResponse['tracks']>([]);
	let loadError = $state<string | null>(null);
	let refreshing = $state(false);

	let selectedId = $state<string | null>(page.url.searchParams.get('sel'));
	let sightings = $state<Sighting[]>([]);
	let bounds = $state<Bounds | null>(null);
	let mapView = $state<MapView>();

	const prefs = (() => {
		try {
			return JSON.parse(localStorage.getItem('map-prefs') ?? '{}');
		} catch {
			return {};
		}
	})();
	let basemap = $state<Basemap>(prefs.basemap ?? (theme.resolved === 'light' ? 'street' : 'dark'));
	let showTracks = $state<boolean>(prefs.showTracks ?? true);
	let cluster = $state<boolean>(prefs.cluster ?? false);
	let heat = $state<boolean>(prefs.heat ?? false);
	let showTable = $state<boolean>(prefs.showTable ?? false);
	let showFilters = $state<boolean>(prefs.showFilters ?? true);

	$effect(() => {
		const value = JSON.stringify({ basemap, showTracks, cluster, heat, showTable, showFilters });
		try {
			localStorage.setItem('map-prefs', value);
		} catch {
			// Storage unavailable: preferences last for this visit only.
		}
	});

	// ---- URL <-> filter state -------------------------------------------------
	// null: nothing written yet, so the first run always reads the URL.
	let written: string | null = null;
	$effect(() => {
		const search = page.url.search;
		if (search === written) return;
		filters.load(page.url.searchParams);
		selectedId = page.url.searchParams.get('sel');
	});

	// replaceState throws until the router has finished its first navigation.
	let routerReady = $state(false);
	afterNavigate(() => (routerReady = true));

	$effect(() => {
		if (!routerReady) return;
		const params = filters.toParams();
		if (demo) params.set('demo', '1');
		if (selectedId) params.set('sel', selectedId);
		const q = params.toString();
		const search = q ? `?${q}` : '';
		if (search === page.url.search) return;
		written = search;
		replaceState(`${page.url.pathname}${search}`, {});
	});

	// ---- data ------------------------------------------------------------------
	$effect(() => {
		const isDemo = demo;
		data = null;
		tracks = [];
		loadError = null;
		loadDevices(isDemo)
			.then((d) => {
				if (isDemo === demo) data = d;
			})
			.catch((err) => (loadError = err instanceof Error ? err.message : String(err)));
		loadTracks(isDemo)
			.then((t) => {
				if (isDemo === demo) tracks = t.tracks;
			})
			.catch(() => {});
	});

	async function refresh() {
		refreshing = true;
		try {
			data = await loadDevices(demo, true);
			tracks = (await loadTracks(demo)).tracks;
		} catch (err) {
			loadError = err instanceof Error ? err.message : String(err);
		} finally {
			refreshing = false;
		}
	}

	// Bounds are only read when "only in view" is on, so panning doesn't refilter everything.
	const result = $derived(
		data
			? applyFilters(data.devices, data.sessions, filters.inView ? bounds : null)
			: { devices: [], error: null }
	);
	const selected = $derived(data?.devices.find((d) => d.id === selectedId) ?? null);
	const visibleTracks = $derived(
		filters.sessions.length ? tracks.filter((t) => filters.sessions.includes(t.session)) : tracks
	);
	const presentSec = $derived(
		SECURITY_ORDER.filter((s) => result.devices.some((d) => d.sec === s))
	);
	// Any filter change re-frames the map on the results. Not while "only what's in view" is on:
	// there the view drives the filter, and fitting would chase its own tail.
	const fitKey = $derived.by(() => {
		if (filters.inView) return '';
		const params = filters.toParams();
		params.sort();
		return `${demo}|${params}`;
	});
	const unlocated = $derived(result.devices.filter((d) => !d.lat && !d.lon).length);

	$effect(() => {
		if (!selected) sightings = [];
	});

	function select(id: string) {
		selectedId = id;
	}

	function selectFromTable(id: string) {
		selectedId = id;
		const d = data?.devices.find((x) => x.id === id);
		if (d && (d.lat || d.lon)) mapView?.flyTo(d.lat, d.lon);
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && selectedId && !(e.target instanceof HTMLInputElement))
			selectedId = null;
	}

	const basemaps: { value: Basemap; label: string }[] = [
		{ value: 'street', label: 'Street' },
		{ value: 'dark', label: 'Dark' },
		{ value: 'satellite', label: 'Satellite' }
	];
</script>

<svelte:head>
	<title>Map · Wardrive Analyzer</title>
</svelte:head>

<svelte:window {onkeydown} />

<div class="layout" class:filters-open={showFilters} class:detail-open={!!selected}>
	{#if showFilters}
		<aside class="filters" aria-label="Filters">
			<div class="filters-head">
				<h2>Filters</h2>
				{#if filters.active}
					<button class="btn small ghost" type="button" onclick={() => filters.reset()}>
						Reset ({filters.active})
					</button>
				{/if}
			</div>
			{#if data}
				<FilterPanel devices={data.devices} sessions={data.sessions} error={result.error} />
			{/if}
		</aside>
	{/if}

	<section class="center" aria-label="Map and results">
		<div class="map-area">
			<MapView
				bind:this={mapView}
				devices={result.devices}
				tracks={visibleTracks}
				{selectedId}
				{sightings}
				{basemap}
				{showTracks}
				{cluster}
				{heat}
				{fitKey}
				onselect={select}
				onbounds={(b) => (bounds = b)}
			/>

			<div class="toolbar top-left">
				<button
					class="btn icon"
					class:on={showFilters}
					type="button"
					onclick={() => (showFilters = !showFilters)}
					aria-label={showFilters ? 'Hide filters' : 'Show filters'}
					aria-pressed={showFilters}
				>
					<Icon name="sliders" size="16px" />
				</button>
				<div class="count" aria-live="polite">
					{#if loadError}
						<span class="error"><Icon name="alert" size="14px" /> {loadError}</span>
					{:else if data}
						<strong class="num">{fmtNum(result.devices.length)}</strong>
						<span class="muted">of {fmtNum(data.devices.length)} devices</span>
						{#if unlocated}<span class="faint">({fmtNum(unlocated)} without GPS)</span>{/if}
					{:else}
						<span class="muted">Loading sessions...</span>
					{/if}
				</div>
				<button
					class="btn icon"
					type="button"
					onclick={refresh}
					disabled={refreshing}
					aria-label="Rescan session files"
					title="Rescan session files"
				>
					<Icon name="refresh" size="15px" class={refreshing ? 'spin' : ''} />
				</button>
			</div>

			<div class="toolbar top-right">
				<div class="segmented" role="radiogroup" aria-label="Basemap">
					{#each basemaps as b (b.value)}
						<button
							type="button"
							role="radio"
							aria-checked={basemap === b.value}
							class:on={basemap === b.value}
							onclick={() => (basemap = b.value)}
						>
							{b.label}
						</button>
					{/each}
				</div>
				<div class="layer-toggles" role="group" aria-label="Layers">
					<button
						type="button"
						class="btn small"
						class:on={showTracks}
						aria-pressed={showTracks}
						onclick={() => (showTracks = !showTracks)}
					>
						<Icon name="route" size="14px" /> Routes
					</button>
					<button
						type="button"
						class="btn small"
						class:on={cluster}
						aria-pressed={cluster}
						onclick={() => (cluster = !cluster)}
					>
						<Icon name="cluster" size="14px" /> Cluster
					</button>
					<button
						type="button"
						class="btn small"
						class:on={heat}
						aria-pressed={heat}
						onclick={() => (heat = !heat)}
					>
						<Icon name="flame" size="14px" /> Heatmap
					</button>
					<button
						type="button"
						class="btn small"
						class:on={showTable}
						aria-pressed={showTable}
						onclick={() => (showTable = !showTable)}
					>
						<Icon name="table" size="14px" /> Table
					</button>
				</div>
			</div>

			{#if data && !cluster}
				<div class="legend" aria-label="Legend">
					{#each presentSec as sec (sec)}
						<span><SecurityMark {sec} size={13} /> {SECURITY[sec].label}</span>
					{/each}
					{#if showTracks && visibleTracks.length}
						<span><i class="swatch-line"></i> Route</span>
					{/if}
					{#if sightings.length}
						<span><i class="swatch-ramp"></i> Heard here (weak → strong)</span>
					{/if}
				</div>
			{/if}

			{#if data && data.sessions.length === 0}
				<div class="empty card">
					<Icon name="info" size="18px" />
					<div>
						<strong>No sessions yet</strong>
						<p class="muted">
							Upload from the Pi, then press rescan. Looking in the directory set by WARDRIVE_DATA.
						</p>
					</div>
				</div>
			{/if}
		</div>

		{#if showTable}
			<div class="table-drawer">
				<DeviceTable devices={result.devices} {selectedId} onselect={selectFromTable} />
			</div>
		{/if}
	</section>

	{#if selected && data}
		<div class="detail-col">
			<DeviceDetail
				device={selected}
				sessions={data.sessions}
				{demo}
				onclose={() => (selectedId = null)}
				onzoom={(lat, lon) => mapView?.flyTo(lat, lon)}
				onsightings={(s) => (sightings = s)}
			/>
		</div>
	{/if}
</div>

<style>
	.layout {
		display: grid;
		grid-template-columns: 1fr;
		height: 100%;
		min-height: 0;
	}

	.layout.filters-open {
		grid-template-columns: 290px 1fr;
	}

	.layout.detail-open {
		grid-template-columns: 1fr 380px;
	}

	.layout.filters-open.detail-open {
		grid-template-columns: 290px 1fr 380px;
	}

	.filters,
	.detail-col {
		min-height: 0;
		overflow-y: auto;
		background: var(--surface);
	}

	.filters {
		border-right: 1px solid var(--border);
	}

	.detail-col {
		border-left: 1px solid var(--border);
	}

	.filters-head {
		position: sticky;
		top: 0;
		z-index: 2;
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 44px;
		padding: 0 14px;
		border-bottom: 1px solid var(--border);
		background: var(--surface);
	}

	.filters-head h2 {
		margin: 0;
		font-size: 14px;
		font-weight: 600;
	}

	.center {
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
	}

	.map-area {
		position: relative;
		flex: 1;
		min-height: 0;
	}

	.table-drawer {
		height: 42%;
		min-height: 180px;
		border-top: 1px solid var(--border);
		background: var(--surface);
	}

	.toolbar {
		position: absolute;
		z-index: 5;
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}

	.top-left {
		top: 10px;
		left: 10px;
		right: 50%;
	}

	.top-right {
		top: 10px;
		right: 10px;
		justify-content: flex-end;
	}

	.toolbar .btn,
	.count,
	.segmented {
		box-shadow: var(--shadow-1);
	}

	.btn.on {
		border-color: var(--accent);
		background: var(--accent-soft);
		color: var(--accent-text);
	}

	.count {
		display: flex;
		align-items: center;
		gap: 5px;
		height: 32px;
		padding: 0 12px;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface);
		white-space: nowrap;
	}

	.count .error {
		display: flex;
		align-items: center;
		gap: 5px;
		color: var(--danger);
		white-space: normal;
	}

	.segmented {
		display: flex;
		padding: 2px;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface);
	}

	.segmented button {
		height: 26px;
		padding: 0 10px;
		border: 0;
		border-radius: 4px;
		background: none;
		color: var(--text-muted);
		font: inherit;
		font-size: 12.5px;
		font-weight: 500;
		cursor: pointer;
	}

	.segmented button.on {
		background: var(--accent-soft);
		color: var(--accent-text);
	}

	.layer-toggles {
		display: flex;
		gap: 6px;
	}

	.layer-toggles .btn {
		background: var(--surface);
	}

	.layer-toggles .btn.on {
		background: var(--accent-soft);
	}

	.legend {
		position: absolute;
		z-index: 5;
		left: 10px;
		bottom: 36px;
		display: flex;
		flex-wrap: wrap;
		gap: 4px 12px;
		max-width: calc(100% - 80px);
		padding: 6px 10px;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: color-mix(in srgb, var(--surface) 92%, transparent);
		box-shadow: var(--shadow-1);
		font-size: 12px;
	}

	.legend span {
		display: inline-flex;
		align-items: center;
		gap: 5px;
	}

	.swatch-line {
		display: inline-block;
		width: 18px;
		height: 3px;
		border-radius: 2px;
		background: #1baf7a;
	}

	.swatch-ramp {
		display: inline-block;
		width: 34px;
		height: 8px;
		border-radius: 4px;
		background: linear-gradient(90deg, #cde2fb, #5598e7, #1c5cab, #0d366b);
	}

	.empty {
		position: absolute;
		z-index: 5;
		top: 50%;
		left: 50%;
		display: flex;
		gap: 10px;
		max-width: 360px;
		padding: 14px 16px;
		transform: translate(-50%, -50%);
		box-shadow: var(--shadow-2);
	}

	.empty p {
		margin: 2px 0 0;
	}

	:global(.spin) {
		animation: spin 0.9s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	@media (max-width: 1100px) {
		.layout.filters-open.detail-open {
			grid-template-columns: 1fr 360px;
		}

		.layout.filters-open.detail-open .filters {
			display: none;
		}
	}

	@media (max-width: 760px) {
		.layout,
		.layout.filters-open,
		.layout.detail-open,
		.layout.filters-open.detail-open {
			grid-template-columns: 1fr;
		}

		.filters,
		.detail-col {
			position: absolute;
			z-index: 20;
			inset: 0;
			display: block !important;
		}

		.top-left {
			right: 10px;
		}

		.top-right {
			top: 54px;
		}
	}
</style>
