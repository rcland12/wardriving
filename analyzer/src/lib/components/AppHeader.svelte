<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import Icon, { type IconName } from './Icon.svelte';
	import ThemeToggle from './ThemeToggle.svelte';

	const demo = $derived(page.url.searchParams.get('demo') === '1');

	// Map and stats share their filters through the query string; sessions only needs the dataset.
	const links: { href: string; label: string; icon: IconName; keepFilters: boolean }[] = [
		{ href: '/', label: 'Map', icon: 'map', keepFilters: true },
		{ href: '/stats', label: 'Stats', icon: 'chart', keepFilters: true },
		{ href: '/sessions', label: 'Sessions', icon: 'route', keepFilters: false }
	];

	function hrefFor(link: (typeof links)[number]): string {
		const current = page.url.pathname;
		const sharesFilters = links.find((l) => l.href === current)?.keepFilters;
		const params = new URLSearchParams(link.keepFilters && sharesFilters ? page.url.search : '');
		params.delete('sel');
		if (demo) params.set('demo', '1');
		const q = params.toString();
		return q ? `${link.href}?${q}` : link.href;
	}

	function setDemo(on: boolean) {
		// Switching datasets clears filters: session names and counts don't carry over.
		goto(on ? `${page.url.pathname}?demo=1` : page.url.pathname);
	}
</script>

<header class="app-header">
	<a class="brand" href={demo ? '/?demo=1' : '/'}>
		<img src="/favicon.svg" alt="" width="22" height="22" />
		<span>Wardrive Analyzer</span>
	</a>

	<nav aria-label="Main">
		{#each links as link (link.href)}
			<a
				href={hrefFor(link)}
				class:active={page.url.pathname === link.href}
				aria-current={page.url.pathname === link.href ? 'page' : undefined}
			>
				<Icon name={link.icon} size="16px" />
				<span>{link.label}</span>
			</a>
		{/each}
	</nav>

	<div class="right">
		<div class="dataset" role="radiogroup" aria-label="Dataset">
			<button
				type="button"
				role="radio"
				aria-checked={!demo}
				class:on={!demo}
				onclick={() => setDemo(false)}
			>
				Captures
			</button>
			<button
				type="button"
				role="radio"
				aria-checked={demo}
				class:on={demo}
				onclick={() => setDemo(true)}
			>
				<Icon name="flask" size="13px" /> Demo
			</button>
		</div>
		<ThemeToggle />
	</div>
</header>

{#if demo}
	<div class="demo-bar" role="status">
		<Icon name="flask" size="14px" /> Showing simulated demo-mode sessions. Nothing here is real, and
		it can never be uploaded to WiGLE.
	</div>
{/if}

<style>
	.app-header {
		display: flex;
		align-items: center;
		gap: 20px;
		height: var(--header-h);
		padding: 0 14px;
		border-bottom: 1px solid var(--border);
		background: var(--surface);
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 9px;
		color: var(--text);
		font-weight: 650;
		white-space: nowrap;
	}

	.brand:hover {
		text-decoration: none;
	}

	nav {
		display: flex;
		gap: 2px;
	}

	nav a {
		display: flex;
		align-items: center;
		gap: 6px;
		height: 32px;
		padding: 0 10px;
		border-radius: var(--r-sm);
		color: var(--text-muted);
		font-weight: 500;
	}

	nav a:hover {
		background: var(--surface-2);
		color: var(--text);
		text-decoration: none;
	}

	nav a.active {
		background: var(--surface-2);
		color: var(--text);
		box-shadow: inset 0 -2px 0 var(--accent);
	}

	.right {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-left: auto;
	}

	.dataset {
		display: flex;
		padding: 2px;
		border: 1px solid var(--border);
		border-radius: var(--r-full);
		background: var(--surface-2);
	}

	.dataset button {
		display: flex;
		align-items: center;
		gap: 4px;
		height: 28px;
		padding: 0 11px;
		border: 0;
		border-radius: var(--r-full);
		background: none;
		color: var(--text-muted);
		font: inherit;
		font-size: 12.5px;
		font-weight: 500;
		cursor: pointer;
	}

	.dataset button.on {
		background: var(--surface);
		color: var(--text);
		box-shadow: var(--shadow-1);
	}

	.demo-bar {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		padding: 5px 12px;
		background: var(--warn-soft);
		color: var(--warn);
		font-size: 12.5px;
		font-weight: 500;
	}

	@media (max-width: 760px) {
		.brand span,
		nav a span {
			display: none;
		}

		.app-header {
			gap: 10px;
		}
	}
</style>
