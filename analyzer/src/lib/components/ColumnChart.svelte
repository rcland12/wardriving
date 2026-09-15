<script lang="ts" module>
	export interface Column {
		key: string;
		label: string;
		/** One value per series, bottom to top. */
		values: number[];
		/** Longer text for the tooltip. */
		title?: string;
	}

	export interface Series {
		label: string;
		color: string;
	}
</script>

<script lang="ts">
	import { fmtNum } from '$lib/format';

	/**
	 * Vertical columns, optionally stacked, on one y-axis. Thin marks (<= 24px) with a 4px rounded
	 * top, a 2px surface gap between stacked segments, hairline grid, and a per-column tooltip.
	 * A legend appears only for two or more series.
	 */
	interface Props {
		columns: Column[];
		series: Series[];
		height?: number;
		/** Show every nth x label when columns are dense. */
		labelEvery?: number;
		ariaLabel: string;
	}

	let { columns, series, height = 200, labelEvery = 1, ariaLabel }: Props = $props();

	let width = $state(480);
	const pad = { top: 12, right: 8, bottom: 26, left: 44 };

	const totals = $derived(columns.map((c) => c.values.reduce((a, b) => a + b, 0)));
	const niceMax = $derived.by(() => {
		const raw = Math.max(1, ...totals);
		const pow = 10 ** Math.floor(Math.log10(raw));
		const step = [1, 2, 2.5, 5, 10].find((m) => raw / (m * pow) <= 5)! * pow;
		return { max: Math.ceil(raw / step) * step, step };
	});
	const ticks = $derived.by(() => {
		const out: number[] = [];
		for (let v = 0; v <= niceMax.max + 1e-9; v += niceMax.step) out.push(v);
		return out;
	});

	const plotW = $derived(Math.max(10, width - pad.left - pad.right));
	const plotH = $derived(height - pad.top - pad.bottom);
	const band = $derived(plotW / Math.max(1, columns.length));
	const barW = $derived(Math.max(3, Math.min(24, band * 0.62)));
	const y = (v: number) => pad.top + plotH - (v / niceMax.max) * plotH;

	let hover = $state<number | null>(null);
</script>

{#if series.length > 1}
	<div class="legend">
		{#each series as s (s.label)}
			<span><i style:background={s.color}></i>{s.label}</span>
		{/each}
	</div>
{/if}

<div class="chart" bind:clientWidth={width}>
	<svg {width} {height} role="img" aria-label={ariaLabel} onpointerleave={() => (hover = null)}>
		{#each ticks as t (t)}
			<line class="grid" x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} />
			<text class="tick" x={pad.left - 6} y={y(t)} dy="0.32em" text-anchor="end">{fmtNum(t)}</text>
		{/each}

		{#each columns as col, i (col.key)}
			{@const cx = pad.left + band * i + band / 2}
			{#if hover === i}
				<rect class="hover" x={pad.left + band * i} y={pad.top} width={band} height={plotH} />
			{/if}
			{#each col.values as v, si (si)}
				{@const below = col.values.slice(0, si).reduce((a, b) => a + b, 0)}
				{@const isTop = col.values.slice(si + 1).every((x) => x === 0)}
				{@const top = y(below + v)}
				{@const bottom = y(below) - (si > 0 ? 2 : 0)}
				{#if v > 0 && bottom - top > 0.5}
					{@const r = isTop ? Math.min(4, (bottom - top) / 2, barW / 2) : 0}
					<path
						d="M{cx - barW / 2},{bottom} V{top + r} Q{cx - barW / 2},{top} {cx -
							barW / 2 +
							r},{top} H{cx + barW / 2 - r} Q{cx + barW / 2},{top} {cx + barW / 2},{top +
							r} V{bottom} Z"
						fill={series[si].color}
					/>
				{/if}
			{/each}
			{#if i % labelEvery === 0}
				<text class="tick" x={cx} y={height - 8} text-anchor="middle">{col.label}</text>
			{/if}
			<!-- Hit target: the whole band, not just the thin mark. -->
			<rect
				class="hit"
				x={pad.left + band * i}
				y={pad.top}
				width={band}
				height={plotH}
				role="presentation"
				onpointerenter={() => (hover = i)}
			/>
		{/each}
		<line class="axis" x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} />
	</svg>

	{#if hover !== null}
		{@const col = columns[hover]}
		<div
			class="tip"
			style:left="{Math.min(Math.max(pad.left + band * hover + band / 2, 70), width - 70)}px"
			style:top="{Math.max(0, y(totals[hover]) - 12)}px"
		>
			<strong>{col.title ?? col.label}</strong>
			{#each series as s, si (s.label)}
				<span class="tip-row">
					{#if series.length > 1}<i style:background={s.color}></i>{/if}
					{series.length > 1 ? `${s.label}: ` : ''}<b class="num">{fmtNum(col.values[si])}</b>
				</span>
			{/each}
		</div>
	{/if}
</div>

<style>
	.chart {
		position: relative;
		width: 100%;
	}

	svg {
		display: block;
		overflow: visible;
	}

	.grid {
		stroke: var(--chart-grid);
		stroke-width: 1;
	}

	.axis {
		stroke: var(--chart-axis);
		stroke-width: 1;
	}

	.tick {
		fill: var(--text-faint);
		font-size: 10.5px;
		font-variant-numeric: tabular-nums;
	}

	.hover {
		fill: var(--surface-2);
	}

	.hit {
		fill: transparent;
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 14px;
		margin-bottom: 6px;
		color: var(--text-muted);
		font-size: 12px;
	}

	.legend span,
	.tip-row {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}

	.legend i,
	.tip-row i {
		display: inline-block;
		width: 10px;
		height: 10px;
		border-radius: 2px;
	}

	.tip {
		position: absolute;
		z-index: 2;
		display: grid;
		gap: 1px;
		padding: 6px 9px;
		transform: translate(-50%, -100%);
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface);
		box-shadow: var(--shadow-2);
		font-size: 12px;
		white-space: nowrap;
		pointer-events: none;
	}

	.tip b {
		font-weight: 600;
	}
</style>
