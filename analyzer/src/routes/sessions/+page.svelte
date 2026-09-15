<script lang="ts">
	import { page } from '$app/state';
	import Icon from '$lib/components/Icon.svelte';
	import ReportView from '$lib/components/ReportView.svelte';
	import UploadDialog from '$lib/components/UploadDialog.svelte';
	import { invalidateDevices, postJson } from '$lib/data';
	import {
		fmtBytes,
		fmtDate,
		fmtDateTime,
		fmtDistance,
		fmtDuration,
		fmtNum,
		fmtTime
	} from '$lib/format';
	import type { ReviewResult, SessionMeta, WigleStatus } from '$lib/types';

	const demo = $derived(page.url.searchParams.get('demo') === '1');

	let sessions = $state<SessionMeta[] | null>(null);
	let indexError = $state<string | null>(null);
	let loadError = $state<string | null>(null);
	let busy = $state<Record<string, string>>({});
	let results = $state<Record<string, { action: string; result: ReviewResult }>>({});
	let uploading = $state<SessionMeta | null>(null);
	let bulk = $state<{ done: number; total: number } | null>(null);

	let wigle = $state<WigleStatus | null>(null);
	let wigleUser = $state<string | null>(null);
	let wigleBusy = $state(false);

	async function load(refresh = false) {
		loadError = null;
		try {
			const res = await fetch(
				`/api/sessions?${demo ? 'demo=1&' : ''}${refresh ? 'refresh=1' : ''}`
			);
			if (!res.ok)
				throw new Error((await res.json().catch(() => null))?.message ?? `HTTP ${res.status}`);
			const body = await res.json();
			sessions = [...body.sessions].reverse();
			indexError = body.indexError;
		} catch (err) {
			loadError = err instanceof Error ? err.message : String(err);
		}
	}

	$effect(() => {
		void demo;
		sessions = null;
		results = {};
		load();
	});

	async function run(s: SessionMeta, action: 'check' | 'fix') {
		busy[s.name] = action;
		try {
			const result = await postJson<ReviewResult>(
				`/api/review/${s.name}/${action}${demo ? '?demo=1' : ''}`
			);
			results[s.name] = { action, result };
		} catch (err) {
			results[s.name] = {
				action,
				result: { ok: false, error: err instanceof Error ? err.message : String(err) }
			};
		} finally {
			delete busy[s.name];
		}
		if (action === 'fix') {
			invalidateDevices();
			await load();
		}
	}

	async function fixAll() {
		const todo = (sessions ?? []).filter((s) => !s.review.reviewed && !s.review.wigle);
		bulk = { done: 0, total: todo.length };
		for (const s of todo) {
			await run(s, 'fix');
			bulk = { done: bulk.done + 1, total: todo.length };
		}
		bulk = null;
	}

	async function wigleStatus() {
		wigleBusy = true;
		try {
			wigle = await (await fetch('/api/wigle/status')).json();
		} catch (err) {
			wigle = { ok: false, error: err instanceof Error ? err.message : String(err) };
		} finally {
			wigleBusy = false;
		}
	}

	async function wigleTest() {
		wigleBusy = true;
		try {
			const body = await (await fetch('/api/wigle/test')).json();
			wigleUser = body.ok ? `Credentials OK: signed in as ${body.user}` : `Failed: ${body.error}`;
		} finally {
			wigleBusy = false;
		}
	}

	const totals = $derived.by(() => {
		const list = sessions ?? [];
		return {
			count: list.length,
			distance: list.reduce((n, s) => n + s.distance, 0),
			time: list.reduce((n, s) => n + Math.max(0, s.end - s.start), 0),
			newDevices: list.reduce((n, s) => n + s.newDevices, 0),
			reviewed: list.filter((s) => s.review.reviewed).length,
			uploaded: list.filter((s) => s.review.wigle).length
		};
	});
	const unreviewed = $derived(
		(sessions ?? []).filter((s) => !s.review.reviewed && !s.review.wigle).length
	);
	const byName = $derived(new Map((sessions ?? []).map((s) => [`${s.name}.csv`, s])));
</script>

<svelte:head>
	<title>Sessions · Wardrive Analyzer</title>
</svelte:head>

<div class="page">
	<div class="head">
		<div>
			<h1>Sessions</h1>
			<p class="muted">
				Captures uploaded from the Pi. Review each one (fix rebuilds the WiGLE CSV from the Kismet
				database), then publish it to WiGLE.
			</p>
		</div>
		<div class="head-actions">
			<button class="btn" type="button" onclick={() => load(true)}>
				<Icon name="refresh" size="14px" /> Rescan
			</button>
			{#if !demo}
				<button class="btn" type="button" onclick={fixAll} disabled={!unreviewed || !!bulk}>
					<Icon name="wrench" size="14px" />
					{bulk
						? `Fixing ${bulk.done + 1} of ${bulk.total}...`
						: `Fix all unreviewed (${unreviewed})`}
				</button>
			{/if}
		</div>
	</div>

	{#if loadError}
		<p class="error card"><Icon name="alert" size="16px" /> {loadError}</p>
	{/if}
	{#if indexError}
		<p class="warning card">
			<Icon name="alert" size="16px" /> Some files could not be read: {indexError}
		</p>
	{/if}

	{#if sessions}
		<div class="tiles">
			<div class="tile">
				<span class="label">Sessions</span><span class="value">{fmtNum(totals.count)}</span>
			</div>
			<div class="tile">
				<span class="label">Driven</span><span class="value">{fmtDistance(totals.distance)}</span>
			</div>
			<div class="tile">
				<span class="label">Capture time</span><span class="value">{fmtDuration(totals.time)}</span>
			</div>
			<div class="tile">
				<span class="label">Unique devices</span><span class="value"
					>{fmtNum(totals.newDevices)}</span
				>
			</div>
			<div class="tile">
				<span class="label">On WiGLE</span>
				<span class="value">{totals.uploaded} <span class="of">/ {totals.count}</span></span>
			</div>
		</div>

		{#if !demo}
			<section class="card wigle">
				<div class="wigle-head">
					<div>
						<h2>WiGLE</h2>
						<p class="muted">
							The API keys are in the server's <code>wardrive/wardrive.env</code> and are only handed
							to the review tool.
						</p>
					</div>
					<div class="head-actions">
						<button class="btn small" type="button" onclick={wigleTest} disabled={wigleBusy}
							>Test credentials</button
						>
						<button class="btn small" type="button" onclick={wigleStatus} disabled={wigleBusy}>
							<Icon name="refresh" size="13px" /> Processing status
						</button>
					</div>
				</div>
				{#if wigleUser}<p class="line">{wigleUser}</p>{/if}
				{#if wigle && !wigle.ok}
					<p class="line danger"><Icon name="x-circle" size="14px" /> {wigle.error}</p>
				{:else if wigle?.transactions}
					<p class="line muted">Queue depth: {wigle.queue_depth ?? '?'}</p>
					{#if wigle.transactions.length}
						<div class="table-scroll">
							<table class="data">
								<thead>
									<tr>
										<th>Transaction</th><th>File</th><th>Status</th>
										<th class="num">Done</th><th class="num">New Wi-Fi</th><th class="num"
											>New BT</th
										>
									</tr>
								</thead>
								<tbody>
									{#each wigle.transactions as t (t.transid)}
										<tr>
											<td class="mono">{t.transid}</td>
											<td
												>{byName.has(t.fileName)
													? fmtDateTime(byName.get(t.fileName)!.start)
													: t.fileName}</td
											>
											<td>{t.status}</td>
											<td class="num">{Math.round(Number(t.percentDone) || 0)}%</td>
											<td class="num">{fmtNum(Number(t.discoveredGps) || 0)}</td>
											<td class="num">{fmtNum(Number(t.btDiscoveredGps) || 0)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{:else}
						<p class="line muted">No uploads on this account yet.</p>
					{/if}
				{/if}
			</section>
		{/if}

		<ol class="list">
			{#each sessions as s (s.name)}
				{@const r = results[s.name]}
				<li class="card session">
					<div class="when">
						<span class="date">{fmtDate(s.start)}</span>
						<span class="time">{fmtTime(s.start)} – {fmtTime(s.end)}</span>
						<code class="faint name">{s.name}</code>
					</div>

					<dl class="stats">
						<div>
							<dt>Duration</dt>
							<dd class="num">{fmtDuration(s.end - s.start)}</dd>
						</div>
						<div>
							<dt>Driven</dt>
							<dd class="num">{fmtDistance(s.distance)}</dd>
						</div>
						<div>
							<dt>Wi-Fi</dt>
							<dd class="num">{fmtNum(s.wifi)}</dd>
						</div>
						<div>
							<dt>Bluetooth</dt>
							<dd class="num">{fmtNum(s.bt)}</dd>
						</div>
						<div>
							<dt>Open</dt>
							<dd class="num">{fmtNum(s.open)}</dd>
						</div>
						<div>
							<dt>New</dt>
							<dd class="num">{fmtNum(s.newDevices)}</dd>
						</div>
					</dl>

					<div class="status">
						{#if s.review.wigle}
							<span
								class="badge ok"
								title="Transaction {s.review.wigle.transids.join(', ')}{s.review.wigle.by
									? ` · by ${s.review.wigle.by}`
									: ''}"
							>
								<Icon name="check-circle" size="13px" /> On WiGLE
							</span>
							<span class="faint small">{fmtDateTime(Date.parse(s.review.wigle.at) / 1000)}</span>
						{:else if s.review.reviewed}
							<span class="badge accent"><Icon name="wrench" size="13px" /> Reviewed</span>
							<span class="faint small"
								>{s.review.reviewed.fixes.length} fix{s.review.reviewed.fixes.length === 1
									? ''
									: 'es'}</span
							>
						{:else}
							<span class="badge"><Icon name="clock" size="13px" /> Not reviewed</span>
						{/if}
						<span class="faint small"
							>{fmtBytes(s.kismetBytes)} · uploaded {fmtDateTime(s.uploadedAt)}</span
						>
					</div>

					<div class="actions">
						<a class="btn small" href="/?s={encodeURIComponent(s.name)}{demo ? '&demo=1' : ''}">
							<Icon name="map" size="14px" /> Map
						</a>
						<button
							class="btn small"
							type="button"
							disabled={!!busy[s.name]}
							onclick={() => run(s, 'check')}
						>
							<Icon name="shield" size="14px" />
							{busy[s.name] === 'check' ? 'Checking...' : 'Check'}
						</button>
						<button
							class="btn small"
							type="button"
							disabled={!!busy[s.name]}
							onclick={() => run(s, 'fix')}
						>
							<Icon name="wrench" size="14px" />
							{busy[s.name] === 'fix' ? 'Fixing...' : 'Fix'}
						</button>
						<button
							class="btn small primary"
							type="button"
							disabled={demo || !!busy[s.name] || !!s.review.wigle}
							title={demo
								? 'Demo sessions can never be uploaded'
								: s.review.wigle
									? 'Already on WiGLE'
									: ''}
							onclick={() => (uploading = s)}
						>
							<Icon name="upload" size="14px" /> WiGLE…
						</button>
					</div>

					{#if r}
						<div class="result">
							<ReportView result={r.result} action={r.action} />
							<button
								class="btn ghost small dismiss"
								type="button"
								onclick={() => delete results[s.name]}
								aria-label="Dismiss"
							>
								<Icon name="x" size="13px" />
							</button>
						</div>
					{/if}
				</li>
			{:else}
				<li class="card empty">No sessions found. Upload from the Pi, then press Rescan.</li>
			{/each}
		</ol>
	{:else if !loadError}
		<p class="muted">Loading sessions...</p>
	{/if}
</div>

{#if uploading}
	<UploadDialog
		session={uploading}
		onclose={(changed) => {
			uploading = null;
			if (changed) {
				invalidateDevices();
				load();
			}
		}}
	/>
{/if}

<style>
	.page {
		height: 100%;
		overflow-y: auto;
		padding: 20px clamp(16px, 3vw, 32px) 48px;
	}

	.page > * {
		max-width: 1180px;
		margin-left: auto;
		margin-right: auto;
	}

	.head {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 12px;
		margin-bottom: 16px;
	}

	h1 {
		margin: 0 0 2px;
		font-size: 22px;
	}

	.head p {
		margin: 0;
		max-width: 640px;
	}

	.head-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}

	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
		gap: 10px;
		margin-bottom: 14px;
	}

	.tile {
		display: grid;
		gap: 2px;
		padding: 12px 14px;
		border: 1px solid var(--border);
		border-radius: var(--r-md);
		background: var(--surface);
	}

	.tile .label {
		color: var(--text-muted);
		font-size: 12.5px;
	}

	.tile .value {
		font-size: 22px;
		font-weight: 600;
	}

	.tile .of {
		color: var(--text-faint);
		font-size: 15px;
		font-weight: 500;
	}

	.wigle {
		display: grid;
		gap: 8px;
		margin-bottom: 14px;
		padding: 14px 16px;
	}

	.wigle-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 10px;
	}

	h2 {
		margin: 0;
		font-size: 15px;
	}

	.wigle-head p {
		margin: 2px 0 0;
		font-size: 12.5px;
	}

	.table-scroll {
		overflow-x: auto;
	}

	.line {
		display: flex;
		align-items: center;
		gap: 6px;
		margin: 0;
	}

	.line.danger,
	.error {
		color: var(--danger);
	}

	.error,
	.warning {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 10px 14px;
	}

	.warning {
		color: var(--warn);
	}

	.list {
		display: grid;
		gap: 10px;
		margin-top: 0;
		margin-bottom: 0;
		padding: 0;
		list-style: none;
	}

	.session {
		display: grid;
		grid-template-columns: 190px 1fr auto;
		grid-template-areas:
			'when stats actions'
			'when status actions'
			'result result result';
		gap: 8px 20px;
		align-items: center;
		padding: 14px 16px;
	}

	.when {
		grid-area: when;
		display: grid;
		align-self: start;
	}

	.date {
		font-size: 15px;
		font-weight: 600;
	}

	.time {
		color: var(--text-muted);
	}

	.name {
		margin-top: 4px;
		font-size: 11px;
	}

	.stats {
		grid-area: stats;
		display: flex;
		flex-wrap: wrap;
		gap: 4px 22px;
		margin: 0;
	}

	.stats dt {
		color: var(--text-faint);
		font-size: 11.5px;
	}

	.stats dd {
		margin: 0;
		font-weight: 600;
	}

	.status {
		grid-area: status;
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
	}

	.small {
		font-size: 12px;
	}

	.actions {
		grid-area: actions;
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: 6px;
		max-width: 300px;
	}

	.result {
		grid-area: result;
		position: relative;
		padding: 10px 36px 10px 12px;
		border-top: 1px solid var(--border);
	}

	.dismiss {
		position: absolute;
		top: 8px;
		right: 0;
	}

	.empty {
		padding: 24px;
		color: var(--text-muted);
		text-align: center;
	}

	@media (max-width: 860px) {
		.session {
			grid-template-columns: 1fr;
			grid-template-areas: 'when' 'stats' 'status' 'actions' 'result';
		}

		.actions {
			justify-content: flex-start;
			max-width: none;
		}
	}
</style>
