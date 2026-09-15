<script lang="ts">
	import { fmtDateTime, fmtNum } from '$lib/format';
	import { BAND_LABEL, SECURITY } from '$lib/security';
	import type { DeviceSummary } from '$lib/types';
	import Icon from './Icon.svelte';
	import SecurityMark from './SecurityMark.svelte';

	interface Props {
		devices: DeviceSummary[];
		selectedId: string | null;
		onselect: (id: string) => void;
	}

	let { devices, selectedId, onselect }: Props = $props();

	type Key =
		'name' | 'mac' | 'sec' | 'signal' | 'channel' | 'manuf' | 'first' | 'sessions' | 'packets';
	const columns: { key: Key; label: string; numeric?: boolean }[] = [
		{ key: 'name', label: 'Name' },
		{ key: 'sec', label: 'Security' },
		{ key: 'signal', label: 'Signal', numeric: true },
		{ key: 'channel', label: 'Channel', numeric: true },
		{ key: 'manuf', label: 'Manufacturer' },
		{ key: 'mac', label: 'MAC' },
		{ key: 'first', label: 'First seen' },
		{ key: 'sessions', label: 'Sessions', numeric: true },
		{ key: 'packets', label: 'Packets', numeric: true }
	];

	const PAGE = 100;
	let sortKey = $state<Key>('signal');
	let desc = $state(true);
	let page = $state(0);

	function value(d: DeviceSummary, key: Key): string | number {
		switch (key) {
			case 'signal':
				return d.signal || -999; // unknown signal sorts as weakest
			case 'channel':
				return Number(d.channel) || 0;
			case 'sessions':
				return d.sessions.length;
			case 'name':
				return d.name.toLowerCase() || '￿'; // unnamed last
			case 'sec':
				return SECURITY[d.sec].label;
			default:
				return d[key];
		}
	}

	const sorted = $derived.by(() => {
		const dir = desc ? -1 : 1;
		return [...devices].sort((a, b) => {
			const va = value(a, sortKey);
			const vb = value(b, sortKey);
			return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
		});
	});
	const pages = $derived(Math.max(1, Math.ceil(sorted.length / PAGE)));
	const rows = $derived(sorted.slice(page * PAGE, page * PAGE + PAGE));

	$effect(() => {
		// Back to the first page whenever the result set or the order changes.
		void devices;
		void sortKey;
		void desc;
		page = 0;
	});

	function sortBy(key: Key) {
		if (sortKey === key) desc = !desc;
		else {
			sortKey = key;
			desc = ['signal', 'first', 'sessions', 'packets'].includes(key);
		}
	}
</script>

<div class="wrap">
	<div class="scroll">
		<table class="data">
			<thead>
				<tr>
					{#each columns as col (col.key)}
						<th
							class:num={col.numeric}
							aria-sort={sortKey === col.key ? (desc ? 'descending' : 'ascending') : 'none'}
						>
							<button type="button" class="sort" onclick={() => sortBy(col.key)}>
								{col.label}
								{#if sortKey === col.key}<span aria-hidden="true">{desc ? '▾' : '▴'}</span>{/if}
							</button>
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each rows as d (d.id)}
					<tr class:selected={d.id === selectedId} onclick={() => onselect(d.id)}>
						<td class="name">
							<button
								type="button"
								class="row-btn"
								onclick={(e) => {
									e.stopPropagation();
									onselect(d.id);
								}}
							>
								<Icon name={d.phy === 'bt' ? 'bluetooth' : 'wifi'} size="13px" />
								{#if d.name}{d.name}{:else}<em class="faint"
										>{d.phy === 'wifi' ? 'hidden' : 'unnamed'}</em
									>{/if}
							</button>
						</td>
						<td
							><span class="sec"
								><SecurityMark sec={d.sec} size={12} /> {SECURITY[d.sec].label}</span
							></td
						>
						<td class="num">{d.signal ? d.signal : '-'}</td>
						<td class="num"
							>{d.phy === 'wifi' ? d.channel : '-'}<span class="faint band"
								>{d.band && d.band !== 'bt'
									? ` ${BAND_LABEL[d.band].replace(' GHz', 'G')}`
									: ''}</span
							></td
						>
						<td class="manuf">{d.manuf && d.manuf !== 'Unknown' ? d.manuf : ''}</td>
						<td class="mono">{d.mac}</td>
						<td class="num">{fmtDateTime(d.first)}</td>
						<td class="num">{d.sessions.length}</td>
						<td class="num">{fmtNum(d.packets)}</td>
					</tr>
				{:else}
					<tr><td colspan={columns.length} class="empty">No devices match these filters.</td></tr>
				{/each}
			</tbody>
		</table>
	</div>
	<div class="pager">
		<span class="muted num">
			{#if sorted.length}{fmtNum(page * PAGE + 1)}-{fmtNum(
					Math.min(sorted.length, (page + 1) * PAGE)
				)} of {fmtNum(sorted.length)}{/if}
		</span>
		<div class="pager-btns">
			<button
				class="btn small icon"
				type="button"
				disabled={page === 0}
				onclick={() => page--}
				aria-label="Previous page"
			>
				<Icon name="chevron-left" size="14px" />
			</button>
			<span class="muted num">{page + 1} / {pages}</span>
			<button
				class="btn small icon"
				type="button"
				disabled={page >= pages - 1}
				onclick={() => page++}
				aria-label="Next page"
			>
				<Icon name="chevron-right" size="14px" />
			</button>
		</div>
	</div>
</div>

<style>
	.wrap {
		display: flex;
		flex-direction: column;
		height: 100%;
		min-height: 0;
	}

	.scroll {
		flex: 1;
		min-height: 0;
		overflow: auto;
	}

	th {
		padding: 0 !important;
	}

	.sort {
		display: flex;
		gap: 4px;
		width: 100%;
		padding: 7px 10px;
		border: 0;
		background: none;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}

	th.num .sort {
		justify-content: flex-end;
	}

	tbody tr {
		cursor: pointer;
	}

	tbody tr:hover {
		background: var(--surface-2);
	}

	tbody tr.selected {
		background: var(--accent-soft);
	}

	.row-btn {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		max-width: 260px;
		padding: 0;
		border: 0;
		background: none;
		color: var(--text);
		font: inherit;
		text-align: left;
		cursor: pointer;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.row-btn :global(.icon) {
		color: var(--text-faint);
	}

	.sec {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}

	.manuf {
		max-width: 200px;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.band {
		font-size: 11px;
	}

	.empty {
		padding: 24px !important;
		color: var(--text-muted);
		text-align: center;
	}

	.pager {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 6px 10px;
		border-top: 1px solid var(--border);
		font-size: 12.5px;
	}

	.pager-btns {
		display: flex;
		align-items: center;
		gap: 8px;
	}
</style>
