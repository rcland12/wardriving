<script lang="ts">
	import { theme, type ThemePreference } from '$lib/theme.svelte';
	import Icon, { type IconName } from './Icon.svelte';

	// Native radios give us arrow-key navigation, roving focus and correct
	// semantics for free — no ARIA bookkeeping required.
	const options: { value: ThemePreference; label: string; icon: IconName }[] = [
		{ value: 'light', label: 'Light', icon: 'sun' },
		{ value: 'dark', label: 'Dark', icon: 'moon' },
		{ value: 'system', label: 'System', icon: 'monitor' }
	];
</script>

<fieldset class="theme-toggle">
	<legend class="sr-only">Color theme</legend>
	{#each options as option (option.value)}
		<div class="option">
			<input
				type="radio"
				name="theme"
				id="theme-{option.value}"
				value={option.value}
				checked={theme.preference === option.value}
				onchange={() => theme.set(option.value)}
			/>
			<label for="theme-{option.value}" title={option.label}>
				<Icon name={option.icon} size="1rem" />
				<span class="sr-only">{option.label}</span>
			</label>
		</div>
	{/each}
</fieldset>

<style>
	.theme-toggle {
		display: flex;
		align-items: center;
		gap: 2px;
		padding: 3px;
		margin: 0;
		border: 1px solid var(--border);
		border-radius: var(--r-full);
		background: var(--surface-2);
	}

	.option {
		display: flex;
	}

	input {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border-width: 0;
	}

	label {
		display: flex;
		align-items: center;
		justify-content: center;
		/* 32px hit area inside a compact control; the header row keeps the
		   surrounding padding so the practical target stays comfortable. */
		width: 2rem;
		height: 2rem;
		border-radius: var(--r-full);
		color: var(--text-muted);
		cursor: pointer;
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}

	label:hover {
		color: var(--text);
		background: var(--surface-inset);
	}

	input:checked + label {
		background: var(--surface);
		color: var(--accent-text);
		box-shadow: var(--shadow-1);
	}

	/* The radio itself is visually hidden, so surface its focus on the label. */
	input:focus-visible + label {
		outline: 2px solid var(--focus);
		outline-offset: 2px;
	}
</style>
