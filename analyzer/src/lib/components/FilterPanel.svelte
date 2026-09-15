<script lang="ts">
	import { BANDS, filters, type TriState } from '$lib/filters.svelte';
	import { fmtNum, sessionLabel } from '$lib/format';
	import { BAND_LABEL, SECURITY, SECURITY_ORDER } from '$lib/security';
	import type { DeviceSummary, SessionMeta } from '$lib/types';
	import Icon from './Icon.svelte';
	import SecurityMark from './SecurityMark.svelte';

	interface Props {
		devices: DeviceSummary[];
		sessions: SessionMeta[];
		error: string | null;
	}

	let { devices, sessions, error }: Props = $props();

	// Facet counts come from the whole dataset, so a checkbox never shows 0 just because
	// another filter hid everything.
	const secCounts = $derived.by(() => {
		const out: Record<string, number> = {};
		for (const d of devices) out[d.sec] = (out[d.sec] ?? 0) + 1;
		return out;
	});
	const bandCounts = $derived.by(() => {
		const out: Record<string, number> = {};
		for (const d of devices) out[d.band] = (out[d.band] ?? 0) + 1;
		return out;
	});
	const manufacturers = $derived.by(() => {
		const out = new Map<string, number>();
		for (const d of devices) if (d.manuf) out.set(d.manuf, (out.get(d.manuf) ?? 0) + 1);
		return [...out.entries()].sort((a, b) => b[1] - a[1]);
	});
	const channels = $derived.by(() => {
		const out = new Map<string, number>();
		for (const d of devices)
			if (d.phy === 'wifi' && d.channel) out.set(d.channel, (out.get(d.channel) ?? 0) + 1);
		return [...out.entries()].sort((a, b) => Number(a[0]) - Number(b[0]));
	});
	const maxSeen = $derived(devices.reduce((max, d) => Math.max(max, d.sessions.length), 1));

	function toggle<T>(list: T[], value: T): T[] {
		return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
	}

	const tri: { value: TriState; label: string }[] = [
		{ value: 'any', label: 'Any' },
		{ value: 'yes', label: 'Yes' },
		{ value: 'no', label: 'No' }
	];
</script>

<div class="panel">
	<div class="search">
		<label class="sr-only" for="filter-q">Search</label>
		<div class="search-box" class:invalid={!!error}>
			<Icon name="search" size="15px" />
			<input
				id="filter-q"
				type="search"
				placeholder={filters.regex ? 'Regular expression' : 'SSID, MAC, manufacturer'}
				bind:value={filters.q}
				autocomplete="off"
				spellcheck="false"
			/>
		</div>
		<label class="inline" title="Treat the search as a regular expression">
			<input type="checkbox" bind:checked={filters.regex} />
			<span class="mono">.*</span>
		</label>
	</div>
	{#if error}
		<p class="error" role="alert"><Icon name="alert" size="14px" /> {error}</p>
	{/if}

	<fieldset>
		<legend>Security</legend>
		{#each SECURITY_ORDER as sec (sec)}
			{#if secCounts[sec]}
				<label class="row">
					<input
						type="checkbox"
						checked={filters.sec.includes(sec)}
						onchange={() => (filters.sec = toggle(filters.sec, sec))}
					/>
					<SecurityMark {sec} />
					<span class="grow">{SECURITY[sec].label}</span>
					<span class="count num">{fmtNum(secCounts[sec] ?? 0)}</span>
				</label>
			{/if}
		{/each}
		<div class="quick">
			<button class="link" type="button" onclick={() => (filters.sec = ['OPEN'])}>Open only</button>
			<button class="link" type="button" onclick={() => (filters.sec = ['OPEN', 'WEP', 'WPA'])}>
				Weak or open
			</button>
			<button class="link" type="button" onclick={() => (filters.sec = [...SECURITY_ORDER])}
				>All</button
			>
		</div>
	</fieldset>

	<fieldset>
		<legend>Radio</legend>
		<div class="chips">
			{#each BANDS as band (band)}
				{#if bandCounts[band]}
					<label class="chip" class:on={filters.bands.includes(band)}>
						<input
							type="checkbox"
							class="sr-only"
							checked={filters.bands.includes(band)}
							onchange={() => (filters.bands = toggle(filters.bands, band))}
						/>
						{BAND_LABEL[band]} <span class="count num">{fmtNum(bandCounts[band])}</span>
					</label>
				{/if}
			{/each}
		</div>
		<label class="field">
			<span>Channel</span>
			<select bind:value={filters.channel}>
				<option value="">Any</option>
				{#each channels as [ch, n] (ch)}
					<option value={ch}>{ch} ({fmtNum(n)})</option>
				{/each}
			</select>
		</label>
		<label class="field">
			<span
				>Min signal <strong class="num"
					>{filters.minSignal > -100 ? `${filters.minSignal} dBm` : 'any'}</strong
				></span
			>
			<input type="range" min="-100" max="-30" step="5" bind:value={filters.minSignal} />
		</label>
	</fieldset>

	<fieldset>
		<legend>Sessions</legend>
		<div class="sessions">
			{#each [...sessions].reverse() as s (s.name)}
				<label class="row">
					<input
						type="checkbox"
						checked={filters.sessions.includes(s.name)}
						onchange={() => (filters.sessions = toggle(filters.sessions, s.name))}
					/>
					<span class="grow">{sessionLabel(s.name, s.start)}</span>
					<span class="count num">{fmtNum(s.wifi + s.bt)}</span>
				</label>
			{/each}
		</div>
		<p class="hint">
			{filters.sessions.length
				? `${filters.sessions.length} selected`
				: 'None checked = all sessions'}
		</p>
		<div class="two">
			<label class="field">
				<span>From</span>
				<input type="date" bind:value={filters.since} />
			</label>
			<label class="field">
				<span>To</span>
				<input type="date" bind:value={filters.until} />
			</label>
		</div>
		{#if maxSeen > 1}
			<label class="field">
				<span
					>Seen in at least <strong class="num">{filters.minSessions}</strong>
					session{filters.minSessions === 1 ? '' : 's'}</span
				>
				<input type="range" min="1" max={maxSeen} step="1" bind:value={filters.minSessions} />
			</label>
		{/if}
	</fieldset>

	<fieldset>
		<legend>Details</legend>
		<div class="field">
			<span>Type</span>
			<div class="chips">
				<label class="chip" class:on={filters.phy.includes('wifi')}>
					<input
						type="checkbox"
						class="sr-only"
						checked={filters.phy.includes('wifi')}
						onchange={() => (filters.phy = toggle(filters.phy, 'wifi'))}
					/>
					<Icon name="wifi" size="13px" /> Wi-Fi
				</label>
				<label class="chip" class:on={filters.phy.includes('bt')}>
					<input
						type="checkbox"
						class="sr-only"
						checked={filters.phy.includes('bt')}
						onchange={() => (filters.phy = toggle(filters.phy, 'bt'))}
					/>
					<Icon name="bluetooth" size="13px" /> Bluetooth
				</label>
			</div>
		</div>
		{#each [{ key: 'hidden', label: 'Hidden SSID' }, { key: 'wps', label: 'WPS enabled' }] as item (item.key)}
			<div class="field">
				<span>{item.label}</span>
				<div class="segmented" role="radiogroup" aria-label={item.label}>
					{#each tri as t (t.value)}
						<label class:on={filters[item.key as 'hidden' | 'wps'] === t.value}>
							<input
								type="radio"
								class="sr-only"
								name="tri-{item.key}"
								value={t.value}
								bind:group={filters[item.key as 'hidden' | 'wps']}
							/>
							{t.label}
						</label>
					{/each}
				</div>
			</div>
		{/each}
		<label class="field">
			<span>Manufacturer</span>
			<select bind:value={filters.manuf}>
				<option value="">Any</option>
				{#each manufacturers as [name, n] (name)}
					<option value={name}>{name} ({fmtNum(n)})</option>
				{/each}
			</select>
		</label>
		<label class="row">
			<input type="checkbox" bind:checked={filters.inView} />
			<span class="grow">Only what's in the map view</span>
		</label>
	</fieldset>
</div>

<style>
	.panel {
		display: grid;
		gap: 14px;
		padding: 12px 14px 20px;
	}

	.search {
		display: flex;
		gap: 8px;
		align-items: center;
	}

	.search-box {
		display: flex;
		flex: 1;
		align-items: center;
		gap: 6px;
		padding-left: 9px;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface-inset);
		color: var(--text-faint);
	}

	.search-box.invalid {
		border-color: var(--danger-dot);
	}

	.search-box input {
		flex: 1;
		border: 0;
		background: transparent;
		padding-left: 0;
	}

	.search-box input:focus-visible {
		outline: none;
	}

	.search-box:focus-within {
		outline: 2px solid var(--focus);
		outline-offset: -1px;
	}

	.error {
		display: flex;
		gap: 6px;
		align-items: center;
		margin: -6px 0 0;
		color: var(--danger);
		font-size: 12px;
	}

	fieldset {
		display: grid;
		gap: 6px;
		margin: 0;
		padding: 12px 0 0;
		border: 0;
		border-top: 1px solid var(--border);
	}

	legend {
		float: left;
		width: 100%;
		margin-bottom: 4px;
		padding: 0;
		color: var(--text-faint);
		font-size: 11px;
		font-weight: 600;
		letter-spacing: 0.06em;
		text-transform: uppercase;
	}

	.row,
	.inline {
		display: flex;
		align-items: center;
		gap: 8px;
		min-height: 24px;
		cursor: pointer;
	}

	.grow {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.count {
		color: var(--text-faint);
		font-size: 12px;
	}

	.quick {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
		margin-top: 2px;
	}

	.link {
		padding: 0;
		border: 0;
		background: none;
		color: var(--accent-text);
		font: inherit;
		font-size: 12.5px;
		cursor: pointer;
	}

	.link:hover {
		text-decoration: underline;
	}

	.chips {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.chip {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		height: 28px;
		padding: 0 10px;
		border: 1px solid var(--border);
		border-radius: var(--r-full);
		color: var(--text-muted);
		font-size: 12.5px;
		cursor: pointer;
		user-select: none;
	}

	.chip.on {
		border-color: var(--accent);
		background: var(--accent-soft);
		color: var(--accent-text);
	}

	.chip:focus-within {
		outline: 2px solid var(--focus);
		outline-offset: 1px;
	}

	.field {
		display: grid;
		gap: 4px;
		font-size: 12.5px;
		color: var(--text-muted);
	}

	.field strong {
		color: var(--text);
		font-weight: 600;
	}

	.two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 8px;
	}

	.sessions {
		display: grid;
		max-height: 190px;
		overflow-y: auto;
		padding-right: 4px;
	}

	.hint {
		margin: 0;
		color: var(--text-faint);
		font-size: 12px;
	}

	.segmented {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		padding: 2px;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface-inset);
	}

	.segmented label {
		display: flex;
		justify-content: center;
		align-items: center;
		height: 24px;
		border-radius: 4px;
		color: var(--text-muted);
		cursor: pointer;
	}

	.segmented label.on {
		background: var(--surface-2);
		color: var(--text);
		box-shadow: var(--shadow-1);
	}

	.segmented label:focus-within {
		outline: 2px solid var(--focus);
	}
</style>
