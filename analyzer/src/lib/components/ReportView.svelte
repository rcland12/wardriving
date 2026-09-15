<script lang="ts">
	import type { ReviewResult } from '$lib/types';
	import Icon from './Icon.svelte';

	// Output of one wardrive_review.py action: verdict, what failed, what was fixed.
	let { result, action }: { result: ReviewResult; action: string } = $props();

	const verdict = $derived(result.report?.verdict);

	// The tool's own messages mention CLI flags; say what to do in this app instead.
	const BLOCKED: Record<string, string> = {
		demo: 'Not uploading: this is simulated demo data and must never reach WiGLE.',
		failures: 'Not uploading until the problems below are fixed.',
		uploaded: 'Already uploaded to WiGLE; not sending it again.'
	};
	const message = $derived(
		result.blocked && BLOCKED[result.blocked] ? BLOCKED[result.blocked] : result.message
	);
</script>

<div class="report">
	{#if !result.ok}
		<p class="line danger"><Icon name="x-circle" size="15px" /> {result.error}</p>
	{:else}
		<div class="head">
			{#if verdict === 'OK'}
				<span class="badge ok"><Icon name="check-circle" size="13px" /> Checks pass</span>
			{:else if verdict === 'WARN'}
				<span class="badge warn"><Icon name="alert" size="13px" /> Warnings</span>
			{:else if verdict === 'FAIL'}
				<span class="badge danger"><Icon name="x-circle" size="13px" /> Needs fixing</span>
			{/if}
			<span class="faint">after {action}</span>
		</div>
		{#if result.fixes?.length}
			<ul class="fixes">
				{#each result.fixes as fix, i (i)}
					<li><Icon name="wrench" size="13px" /> {fix}</li>
				{/each}
			</ul>
		{:else if result.fixes}
			<p class="line muted">
				Nothing needed fixing; the reviewed copy is identical to the original.
			</p>
		{/if}
		{#if result.sent}
			<p class="line ok"><Icon name="upload" size="14px" /> {result.message}</p>
		{:else if message}
			<p class="line" class:muted={result.dry_run}>{message}</p>
		{/if}
		{#each result.report?.failures ?? [] as text, i (i)}
			<p class="line danger"><Icon name="x-circle" size="14px" /> {text}</p>
		{/each}
		{#each result.report?.warnings ?? [] as text, i (i)}
			<p class="line warn"><Icon name="alert" size="14px" /> {text}</p>
		{/each}
		{#if result.report?.info.length}
			<pre>{result.report.info.join('\n')}</pre>
		{/if}
	{/if}
</div>

<style>
	.report {
		display: grid;
		gap: 6px;
	}

	.head {
		display: flex;
		align-items: center;
		gap: 8px;
		font-size: 12px;
	}

	.line {
		display: flex;
		align-items: flex-start;
		gap: 6px;
		margin: 0;
		font-size: 13px;
	}

	.line :global(.icon) {
		margin-top: 2px;
	}

	.line.danger {
		color: var(--danger);
	}

	.line.warn {
		color: var(--warn);
	}

	.line.ok {
		color: var(--ok);
	}

	.fixes {
		display: grid;
		gap: 3px;
		margin: 0;
		padding: 0;
		list-style: none;
		font-size: 13px;
	}

	.fixes li {
		display: flex;
		align-items: flex-start;
		gap: 6px;
	}

	.fixes :global(.icon) {
		margin-top: 2px;
		color: var(--ok-dot);
	}

	pre {
		margin: 2px 0 0;
		padding: 8px 10px;
		overflow-x: auto;
		border: 1px solid var(--border);
		border-radius: var(--r-sm);
		background: var(--surface-inset);
		color: var(--text-muted);
		font-family: var(--font-mono);
		font-size: 11.5px;
	}
</style>
