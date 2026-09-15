<script lang="ts">
	import { onMount } from 'svelte';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import { theme } from '$lib/theme.svelte';
	import '../app.css';

	let { children } = $props();

	// The inline script in app.html already set data-theme before first paint.
	// init() picks that stored preference up into state and starts tracking the
	// OS setting; the effect below keeps <html> in sync from then on.
	onMount(() => theme.init());

	$effect(() => {
		theme.apply();
	});
</script>

<svelte:head>
	<title>Wardrive Analyzer</title>
</svelte:head>

<a class="skip-link" href="#main">Skip to main content</a>

<div class="shell">
	<AppHeader />
	<main id="main">
		{@render children?.()}
	</main>
</div>

<style>
	.shell {
		display: flex;
		flex-direction: column;
		height: 100vh;
		height: 100dvh;
	}

	main {
		position: relative;
		flex: 1;
		min-height: 0;
	}
</style>
