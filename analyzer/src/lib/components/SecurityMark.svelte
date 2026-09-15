<script lang="ts">
	import { SECURITY } from '$lib/security';
	import type { Security } from '$lib/types';

	// The same marker the map draws, as inline SVG for legends, lists and badges.
	let { sec, size = 14 }: { sec: Security; size?: number } = $props();

	const style = $derived(SECURITY[sec]);
	const outline = '#0b0f14';
</script>

<svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" class="mark">
	{#if style.shape === 'circle'}
		<circle cx="10" cy="10" r="7" fill={style.color} stroke={outline} stroke-width="2" />
	{:else if style.shape === 'square'}
		<rect
			x="3.5"
			y="3.5"
			width="13"
			height="13"
			rx="1.5"
			fill={style.color}
			stroke={outline}
			stroke-width="2"
		/>
	{:else if style.shape === 'diamond'}
		<path
			d="M10 1.8 18.2 10 10 18.2 1.8 10Z"
			fill={style.color}
			stroke={outline}
			stroke-width="2"
			stroke-linejoin="round"
		/>
	{:else if style.shape === 'triangle'}
		<path
			d="M10 2.2 18.5 17H1.5Z"
			fill={style.color}
			stroke={outline}
			stroke-width="2"
			stroke-linejoin="round"
		/>
	{:else if style.shape === 'plus'}
		<path
			d="M7.5 2.5h5v5h5v5h-5v5h-5v-5h-5v-5h5Z"
			fill={style.color}
			stroke={outline}
			stroke-width="1.6"
			stroke-linejoin="round"
		/>
	{:else}
		<circle cx="10" cy="10" r="6.5" fill="none" stroke={outline} stroke-width="5" />
		<circle cx="10" cy="10" r="6.5" fill="none" stroke={style.color} stroke-width="3" />
	{/if}
</svg>

<style>
	.mark {
		display: inline-block;
		flex-shrink: 0;
		vertical-align: -2px;
	}
</style>
