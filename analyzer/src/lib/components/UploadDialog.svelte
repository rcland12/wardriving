<script lang="ts">
	import { onMount } from 'svelte';
	import { postJson } from '$lib/data';
	import { fmtBytes, fmtNum, sessionLabel } from '$lib/format';
	import type { ReviewResult, SessionMeta } from '$lib/types';
	import Icon from './Icon.svelte';
	import ReportView from './ReportView.svelte';

	/**
	 * Publishing to WiGLE is public and permanent, so it takes three steps: a dry run through the
	 * same checks the upload uses, a summary of exactly what will be sent, and an explicit
	 * confirmation. The server also refuses an upload whose body doesn't name the session.
	 */
	interface Props {
		session: SessionMeta;
		onclose: (changed: boolean) => void;
	}

	let { session, onclose }: Props = $props();

	let dialog: HTMLDialogElement;
	let stage = $state<'checking' | 'ready' | 'blocked' | 'uploading' | 'done' | 'error'>('checking');
	let dryRun = $state<ReviewResult | null>(null);
	let upload = $state<ReviewResult | null>(null);
	let confirmed = $state(false);
	let fixing = $state(false);
	let changed = false;

	async function runDryRun() {
		stage = 'checking';
		confirmed = false;
		try {
			dryRun = await postJson<ReviewResult>(`/api/review/${session.name}/dry-run`);
			stage = dryRun.ok && dryRun.dry_run ? 'ready' : dryRun.ok ? 'blocked' : 'error';
		} catch (err) {
			dryRun = { ok: false, error: err instanceof Error ? err.message : String(err) };
			stage = 'error';
		}
	}

	async function fixThenRecheck() {
		fixing = true;
		try {
			await postJson<ReviewResult>(`/api/review/${session.name}/fix`);
			changed = true;
		} finally {
			fixing = false;
		}
		await runDryRun();
	}

	async function send() {
		stage = 'uploading';
		try {
			upload = await postJson<ReviewResult>(`/api/review/${session.name}/upload`, {
				confirm: session.name
			});
			changed = true;
			stage = upload.ok && upload.sent ? 'done' : 'error';
		} catch (err) {
			upload = { ok: false, error: err instanceof Error ? err.message : String(err) };
			stage = 'error';
		}
	}

	onMount(() => {
		dialog.showModal();
		runDryRun();
	});
</script>

<dialog
	bind:this={dialog}
	aria-labelledby="upload-title"
	onclose={() => onclose(changed)}
	oncancel={(e) => {
		if (stage === 'uploading') e.preventDefault();
	}}
>
	<header>
		<div>
			<p class="eyebrow">Publish to WiGLE</p>
			<h2 id="upload-title">{sessionLabel(session.name, session.start)}</h2>
			<code class="faint">{session.name}</code>
		</div>
		<button
			class="btn ghost icon"
			type="button"
			onclick={() => dialog.close()}
			disabled={stage === 'uploading'}
			aria-label="Close"
		>
			<Icon name="x" size="16px" />
		</button>
	</header>

	<div class="body">
		{#if stage === 'checking'}
			<p class="status">
				<Icon name="refresh" size="15px" class="spin" /> Running the pre-upload checks...
			</p>
		{:else if stage === 'blocked' && dryRun}
			<ReportView result={dryRun} action="dry run" />
			{#if dryRun.blocked === 'failures'}
				<p class="hint">
					Fix rebuilds security details and timestamps from the Kismet database into a reviewed
					copy. The original upload is never modified.
				</p>
			{/if}
		{:else if stage === 'ready' && dryRun}
			<dl class="summary">
				<div>
					<dt>File</dt>
					<dd><code>{dryRun.file}</code> ({fmtBytes(dryRun.bytes ?? 0)})</dd>
				</div>
				<div>
					<dt>Rows</dt>
					<dd class="num">{fmtNum(dryRun.rows ?? 0)}</dd>
				</div>
				<div>
					<dt>Source</dt>
					<dd>
						{#if dryRun.reviewed}
							Reviewed copy <span class="badge ok"><Icon name="check" size="12px" /> fixed</span>
						{:else}
							Original upload <span class="badge warn"
								><Icon name="alert" size="12px" /> not reviewed</span
							>
						{/if}
					</dd>
				</div>
				<div>
					<dt>Commercial use</dt>
					<dd>{dryRun.donate ? 'Allowed (WIGLE_DONATE=on)' : 'Not allowed (WIGLE_DONATE=off)'}</dd>
				</div>
			</dl>
			<ReportView result={dryRun} action="dry run" />
			<label class="confirm">
				<input type="checkbox" bind:checked={confirmed} />
				<span>
					I understand this publishes these networks and their locations on WiGLE, where they become
					public and can't be withdrawn from this app.
				</span>
			</label>
		{:else if stage === 'uploading'}
			<p class="status">
				<Icon name="upload" size="15px" /> Uploading to WiGLE... this can take a minute.
			</p>
		{:else if stage === 'done' && upload}
			<ReportView result={upload} action="upload" />
			<p class="hint">
				WiGLE processes uploads in a queue; check progress with "WiGLE status" on the Sessions page.
			</p>
		{:else if stage === 'error'}
			<ReportView
				result={upload ?? dryRun ?? { ok: false, error: 'unknown error' }}
				action="upload"
			/>
		{/if}
	</div>

	<footer>
		{#if stage === 'blocked' && dryRun?.blocked === 'failures'}
			<button class="btn" type="button" onclick={() => dialog.close()}>Cancel</button>
			<button class="btn primary" type="button" onclick={fixThenRecheck} disabled={fixing}>
				<Icon name="wrench" size="14px" />
				{fixing ? 'Fixing...' : 'Fix and re-check'}
			</button>
		{:else if stage === 'ready'}
			<button class="btn" type="button" onclick={() => dialog.close()}>Cancel</button>
			<button class="btn primary" type="button" onclick={send} disabled={!confirmed}>
				<Icon name="upload" size="14px" /> Upload to WiGLE
			</button>
		{:else if stage !== 'checking' && stage !== 'uploading'}
			<button class="btn" type="button" onclick={() => dialog.close()}>Close</button>
		{/if}
	</footer>
</dialog>

<style>
	dialog {
		width: min(560px, calc(100vw - 32px));
		max-height: calc(100dvh - 48px);
		padding: 0;
		border: 1px solid var(--border);
		border-radius: var(--r-lg);
		background: var(--surface);
		color: var(--text);
		box-shadow: var(--shadow-2);
	}

	dialog::backdrop {
		background: rgba(1, 4, 9, 0.6);
	}

	header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 10px;
		padding: 16px 18px 12px;
		border-bottom: 1px solid var(--border);
	}

	h2 {
		margin: 0;
		font-size: 17px;
	}

	.body {
		display: grid;
		gap: 12px;
		padding: 16px 18px;
	}

	.status {
		display: flex;
		align-items: center;
		gap: 8px;
		margin: 0;
		color: var(--text-muted);
	}

	.summary {
		display: grid;
		margin: 0;
		border-top: 1px solid var(--border);
	}

	.summary div {
		display: grid;
		grid-template-columns: 130px 1fr;
		gap: 10px;
		padding: 6px 0;
		border-bottom: 1px solid var(--border);
	}

	dt {
		color: var(--text-muted);
	}

	dd {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px;
		margin: 0;
	}

	.confirm {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 10px 12px;
		border: 1px solid var(--border);
		border-radius: var(--r-md);
		background: var(--surface-2);
		cursor: pointer;
	}

	.confirm input {
		margin-top: 3px;
	}

	.hint {
		margin: 0;
		color: var(--text-muted);
		font-size: 12.5px;
	}

	footer {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		padding: 12px 18px 16px;
		border-top: 1px solid var(--border);
	}
</style>
