<script lang="ts">
	import { fmtTime } from '$lib/format';
	import type { Sighting } from '$lib/types';

	// Signal strength over one session: a single series, so no legend - the heading names it.
	let { sightings }: { sightings: Sighting[] } = $props();

	let width = $state(320);
	const height = 150;
	const pad = { top: 10, right: 12, bottom: 24, left: 36 };

	const points = $derived(sightings.filter((s) => s.signal !== 0));
	const t0 = $derived(points[0]?.ts ?? 0);
	const t1 = $derived(Math.max(t0 + 1, points[points.length - 1]?.ts ?? 0));
	const yMin = $derived(Math.floor((Math.min(...points.map((p) => p.signal), -80) - 5) / 10) * 10);
	const yMax = $derived(Math.ceil((Math.max(...points.map((p) => p.signal), -40) + 5) / 10) * 10);

	const x = (ts: number) => pad.left + ((ts - t0) / (t1 - t0)) * (width - pad.left - pad.right);
	const y = (dbm: number) =>
		pad.top + ((yMax - dbm) / (yMax - yMin)) * (height - pad.top - pad.bottom);

	const ticks = $derived.by(() => {
		const out: number[] = [];
		const step = yMax - yMin > 50 ? 20 : 10;
		for (let v = yMax; v >= yMin; v -= step) out.push(v);
		return out;
	});
	const path = $derived(
		points.map((p, i) => `${i ? 'L' : 'M'}${x(p.ts).toFixed(1)},${y(p.signal).toFixed(1)}`).join('')
	);

	let hover = $state<number | null>(null);

	function onmove(e: PointerEvent) {
		const svg = e.currentTarget as SVGSVGElement;
		const px = e.clientX - svg.getBoundingClientRect().left;
		let best = 0;
		for (let i = 1; i < points.length; i++) {
			if (Math.abs(x(points[i].ts) - px) < Math.abs(x(points[best].ts) - px)) best = i;
		}
		hover = points.length ? best : null;
	}
</script>

{#if points.length}
	<div class="chart" bind:clientWidth={width}>
		<svg
			{width}
			{height}
			role="img"
			aria-label="Signal strength over time, {points.length} readings from {Math.min(
				...points.map((p) => p.signal)
			)} to {Math.max(...points.map((p) => p.signal))} dBm"
			onpointermove={onmove}
			onpointerleave={() => (hover = null)}
		>
			{#each ticks as v (v)}
				<line class="grid" x1={pad.left} x2={width - pad.right} y1={y(v)} y2={y(v)} />
				<text class="tick" x={pad.left - 6} y={y(v)} dy="0.32em" text-anchor="end">{v}</text>
			{/each}
			<line
				class="axis"
				x1={pad.left}
				x2={width - pad.right}
				y1={height - pad.bottom}
				y2={height - pad.bottom}
			/>
			<text class="tick" x={pad.left} y={height - 6}>{fmtTime(t0)}</text>
			<text class="tick" x={width - pad.right} y={height - 6} text-anchor="end">{fmtTime(t1)}</text>

			{#if points.length > 1}
				<path class="line" d={path} />
			{/if}
			{#if hover !== null}
				<line
					class="crosshair"
					x1={x(points[hover].ts)}
					x2={x(points[hover].ts)}
					y1={pad.top}
					y2={height - pad.bottom}
				/>
			{/if}
			{#each points as p, i (i)}
				<circle
					class="dot"
					class:active={hover === i}
					cx={x(p.ts)}
					cy={y(p.signal)}
					r={hover === i ? 5 : 4}
				/>
			{/each}
		</svg>
		{#if hover !== null}
			{@const p = points[hover]}
			<div
				class="tip"
				style:left="{Math.min(Math.max(x(p.ts), 60), width - 60)}px"
				style:top="{Math.max(0, y(p.signal) - 44)}px"
			>
				<strong class="num">{p.signal} dBm</strong>
				<span class="num">{new Date(p.ts * 1000).toLocaleTimeString()}</span>
			</div>
		{/if}
	</div>
{:else}
	<p class="none">No located readings with a signal level in this session.</p>
{/if}

<style>
	.chart {
		position: relative;
		width: 100%;
	}

	svg {
		display: block;
		overflow: visible;
		touch-action: none;
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

	.line {
		fill: none;
		stroke: var(--series-1);
		stroke-width: 2;
		stroke-linejoin: round;
		stroke-linecap: round;
	}

	.crosshair {
		stroke: var(--chart-axis);
		stroke-width: 1;
	}

	.dot {
		fill: var(--series-1);
		stroke: var(--surface);
		stroke-width: 2;
	}

	.tip {
		position: absolute;
		display: grid;
		transform: translateX(-50%);
		padding: 4px 8px;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface);
		box-shadow: var(--shadow-2);
		font-size: 12px;
		white-space: nowrap;
		pointer-events: none;
	}

	.tip span {
		color: var(--text-muted);
	}

	.none {
		margin: 0;
		color: var(--text-muted);
		font-size: 12.5px;
	}
</style>
