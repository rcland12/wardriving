<script lang="ts" module>
	import type { Security } from '$lib/types';

	export interface BarRow {
		key: string;
		label: string;
		value: number;
		/** Mark color; defaults to the single-series blue. */
		color?: string;
		/** Security marker drawn beside the label, so identity never relies on color alone. */
		sec?: Security;
	}
</script>

<script lang="ts">
	import { fmtNum, fmtPct } from '$lib/format';
	import SecurityMark from './SecurityMark.svelte';

	// Horizontal bars for ranked categories: thin marks from one baseline, value at the tip.
	let { rows, total, onpick }: { rows: BarRow[]; total: number; onpick?: (row: BarRow) => void } =
		$props();

	const max = $derived(rows.reduce((m, r) => Math.max(m, r.value), 0) || 1);
</script>

<ul class="bars">
	{#each rows as row (row.key)}
		<li>
			<svelte:element
				this={onpick ? 'button' : 'div'}
				class="row"
				type={onpick ? 'button' : undefined}
				onclick={onpick ? () => onpick(row) : undefined}
				title="{row.label}: {fmtNum(row.value)} ({fmtPct(row.value, total)})"
				role={onpick ? undefined : 'group'}
			>
				<span class="label">
					{#if row.sec}<SecurityMark sec={row.sec} size={12} />{/if}
					<span class="text">{row.label}</span>
				</span>
				<span class="track">
					<span
						class="bar"
						style:width="{Math.max(0.4, (100 * row.value) / max)}%"
						style:background={row.color ?? 'var(--series-1)'}
					></span>
					<span class="value num"
						>{fmtNum(row.value)} <span class="pct">{fmtPct(row.value, total)}</span></span
					>
				</span>
			</svelte:element>
		</li>
	{/each}
</ul>

<style>
	.bars {
		display: grid;
		gap: 2px;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.row {
		display: grid;
		grid-template-columns: minmax(90px, 38%) 1fr;
		align-items: center;
		gap: 10px;
		width: 100%;
		min-height: 26px;
		padding: 2px 6px;
		border: 0;
		border-radius: var(--r-sm);
		background: none;
		color: var(--text);
		font: inherit;
		text-align: left;
	}

	button.row {
		cursor: pointer;
	}

	button.row:hover {
		background: var(--surface-2);
	}

	.label {
		display: flex;
		align-items: center;
		gap: 6px;
		min-width: 0;
		font-size: 13px;
	}

	.text {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.track {
		display: flex;
		align-items: center;
		gap: 8px;
		min-width: 0;
	}

	.bar {
		flex-shrink: 0;
		max-width: calc(100% - 92px);
		height: 14px;
		border-radius: 0 4px 4px 0;
	}

	.value {
		color: var(--text);
		font-size: 12.5px;
		white-space: nowrap;
	}

	.pct {
		color: var(--text-faint);
	}
</style>
