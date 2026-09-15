<script lang="ts" module>
	export type Basemap = 'street' | 'dark' | 'satellite';
</script>

<script lang="ts">
	import 'maplibre-gl/dist/maplibre-gl.css';
	import type {
		GeoJSONSource,
		LngLatBoundsLike,
		Map as MapLibreMap,
		MapGeoJSONFeature,
		MapLayerMouseEvent,
		Popup,
		StyleSpecification
	} from 'maplibre-gl';
	import { onMount } from 'svelte';
	import type { Bounds } from '$lib/filters.svelte';
	import { markerImages } from '$lib/markers';
	import { SECURITY } from '$lib/security';
	import type { DeviceSummary, Security, Sighting, TracksResponse } from '$lib/types';

	interface Props {
		devices: DeviceSummary[];
		tracks: TracksResponse['tracks'];
		selectedId: string | null;
		sightings: Sighting[];
		basemap: Basemap;
		showTracks: boolean;
		cluster: boolean;
		heat: boolean;
		/**
		 * When this changes, the map fits itself to the devices (debounced, so typing a search
		 * settles first). An empty string turns automatic fitting off.
		 */
		fitKey: string;
		onselect: (id: string) => void;
		onbounds: (bounds: Bounds) => void;
	}

	let {
		devices,
		tracks,
		selectedId,
		sightings,
		basemap,
		showTracks,
		cluster,
		heat,
		fitKey,
		onselect,
		onbounds
	}: Props = $props();

	let container: HTMLDivElement;
	let map = $state<MapLibreMap | null>(null);
	let ready = $state(false);

	const OSM = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

	const style: StyleSpecification = {
		version: 8,
		glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf',
		sources: {
			street: {
				type: 'raster',
				tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
				tileSize: 256,
				maxzoom: 19,
				attribution: OSM
			},
			satellite: {
				type: 'raster',
				tiles: [
					'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
				],
				tileSize: 256,
				maxzoom: 19,
				attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community'
			},
			'satellite-roads': {
				type: 'raster',
				tiles: [
					'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}'
				],
				tileSize: 256,
				maxzoom: 19
			},
			'satellite-labels': {
				type: 'raster',
				tiles: [
					'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'
				],
				tileSize: 256,
				maxzoom: 19
			}
		},
		layers: [
			{ id: 'street', type: 'raster', source: 'street' },
			// Dark streets: the OSM tiles with lightness inverted and hue rotated back, so water
			// stays blue and parks green. CARTO's dark tiles now watermark keyless browser requests.
			{
				id: 'dark',
				type: 'raster',
				source: 'street',
				layout: { visibility: 'none' },
				paint: {
					'raster-brightness-min': 0.92,
					'raster-brightness-max': 0.08,
					'raster-hue-rotate': 180,
					'raster-saturation': -0.55,
					'raster-contrast': 0.08
				}
			},
			{ id: 'satellite', type: 'raster', source: 'satellite', layout: { visibility: 'none' } },
			{
				id: 'satellite-roads',
				type: 'raster',
				source: 'satellite-roads',
				layout: { visibility: 'none' },
				paint: { 'raster-opacity': 0.55 }
			},
			{
				id: 'satellite-labels',
				type: 'raster',
				source: 'satellite-labels',
				layout: { visibility: 'none' }
			}
		]
	};

	const BASEMAP_LAYERS: Record<Basemap, string[]> = {
		street: ['street'],
		dark: ['dark'],
		satellite: ['satellite', 'satellite-roads', 'satellite-labels']
	};

	const EMPTY = { type: 'FeatureCollection' as const, features: [] };

	function deviceFeatures(list: DeviceSummary[]) {
		return {
			type: 'FeatureCollection' as const,
			features: list
				.filter((d) => d.lat || d.lon)
				.map((d) => ({
					type: 'Feature' as const,
					geometry: { type: 'Point' as const, coordinates: [d.lon, d.lat] },
					properties: { id: d.id, sec: d.sec, name: d.name, signal: d.signal, mac: d.mac }
				}))
		};
	}

	const iconSize = ['interpolate', ['linear'], ['zoom'], 11, 0.45, 15, 0.7, 18, 1] as const;

	function addLayers(m: MapLibreMap) {
		for (const { name, image } of markerImages()) m.addImage(name, image, { pixelRatio: 2 });

		m.addSource('tracks', { type: 'geojson', data: EMPTY });
		m.addSource('devices', { type: 'geojson', data: EMPTY });
		m.addSource('clusters', {
			type: 'geojson',
			data: EMPTY,
			cluster: true,
			clusterRadius: 44,
			clusterMaxZoom: 17,
			clusterProperties: { open: ['+', ['case', ['==', ['get', 'sec'], 'OPEN'], 1, 0]] }
		});
		m.addSource('sightings', { type: 'geojson', data: EMPTY });
		m.addSource('selected', { type: 'geojson', data: EMPTY });

		m.addLayer({
			id: 'tracks-casing',
			type: 'line',
			source: 'tracks',
			layout: { 'line-join': 'round', 'line-cap': 'round' },
			paint: { 'line-color': '#0b0f14', 'line-width': 5, 'line-opacity': 0.45 }
		});
		m.addLayer({
			id: 'tracks',
			type: 'line',
			source: 'tracks',
			layout: { 'line-join': 'round', 'line-cap': 'round' },
			paint: { 'line-color': '#1baf7a', 'line-width': 2.5, 'line-opacity': 0.9 }
		});
		// Density of networks: one blue hue, transparent where there are none.
		m.addLayer({
			id: 'heat',
			type: 'heatmap',
			source: 'devices',
			layout: { visibility: 'none' },
			paint: {
				'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 8, 15, 22, 18, 40],
				'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 16, 1.4],
				'heatmap-opacity': 0.8,
				'heatmap-color': [
					'interpolate',
					['linear'],
					['heatmap-density'],
					0,
					'rgba(57,135,229,0)',
					0.2,
					'rgba(57,135,229,0.45)',
					0.5,
					'#3987e5',
					0.8,
					'#9ec5f4',
					1,
					'#ffffff'
				]
			}
		});
		m.addLayer({
			id: 'devices',
			type: 'symbol',
			source: 'devices',
			layout: {
				'icon-image': ['concat', 'sec-', ['get', 'sec']],
				'icon-size': iconSize as never,
				'icon-allow-overlap': true,
				'icon-ignore-placement': true,
				// Strongest signal drawn last, on top.
				'symbol-sort-key': ['get', 'signal']
			}
		});
		m.addLayer({
			id: 'cluster-circles',
			type: 'circle',
			source: 'clusters',
			filter: ['has', 'point_count'],
			layout: { visibility: 'none' },
			paint: {
				'circle-color': '#2a78d6',
				'circle-opacity': 0.85,
				'circle-radius': ['step', ['get', 'point_count'], 13, 25, 17, 100, 22, 500, 28],
				'circle-stroke-width': 2,
				'circle-stroke-color': '#0b0f14'
			}
		});
		m.addLayer({
			id: 'cluster-count',
			type: 'symbol',
			source: 'clusters',
			filter: ['has', 'point_count'],
			layout: {
				visibility: 'none',
				'text-field': ['get', 'point_count_abbreviated'],
				'text-font': ['Noto Sans Medium'],
				'text-size': 12,
				'text-allow-overlap': true
			},
			paint: { 'text-color': '#ffffff' }
		});
		m.addLayer({
			id: 'cluster-points',
			type: 'symbol',
			source: 'clusters',
			filter: ['!', ['has', 'point_count']],
			layout: {
				visibility: 'none',
				'icon-image': ['concat', 'sec-', ['get', 'sec']],
				'icon-size': iconSize as never,
				'icon-allow-overlap': true,
				'icon-ignore-placement': true
			}
		});
		// Where the selected device was heard: darker/brighter = stronger, from one blue ramp.
		m.addLayer({
			id: 'sightings',
			type: 'circle',
			source: 'sightings',
			paint: {
				'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 3, 17, 7],
				'circle-color': [
					'interpolate',
					['linear'],
					['get', 'signal'],
					-95,
					'#cde2fb',
					-75,
					'#5598e7',
					-55,
					'#1c5cab',
					-35,
					'#0d366b'
				],
				'circle-stroke-width': 1.5,
				'circle-stroke-color': '#ffffff'
			}
		});
		m.addLayer({
			id: 'selected-casing',
			type: 'circle',
			source: 'selected',
			paint: {
				'circle-radius': 17,
				'circle-color': 'rgba(0,0,0,0)',
				'circle-stroke-width': 6,
				'circle-stroke-color': '#0b0f14'
			}
		});
		m.addLayer({
			id: 'selected',
			type: 'circle',
			source: 'selected',
			paint: {
				'circle-radius': 17,
				'circle-color': 'rgba(0,0,0,0)',
				'circle-stroke-width': 3,
				'circle-stroke-color': '#ffffff'
			}
		});
	}

	function popupHtml(features: MapGeoJSONFeature[]): string {
		const f = features[0].properties as {
			name: string;
			sec: Security;
			signal: number;
			mac: string;
		};
		const esc = (s: string) => s.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`);
		const more =
			features.length > 1 ? `<div class="pop-more">+${features.length - 1} more here</div>` : '';
		return `<div class="pop"><strong>${f.name ? esc(f.name) : '<em>hidden / unnamed</em>'}</strong>
			<div class="pop-sub">${esc(SECURITY[f.sec].label)} · ${f.signal ? `${f.signal} dBm` : 'no signal'}</div>
			<div class="pop-mac">${esc(f.mac)}</div>${more}</div>`;
	}

	export function flyTo(lat: number, lon: number) {
		if (!map) return;
		map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 17), speed: 1.6 });
	}

	export function fitTo(points: [number, number][]) {
		if (!map || !points.length) return;
		let [w, s, e, n] = [Infinity, Infinity, -Infinity, -Infinity];
		for (const [lon, lat] of points) {
			w = Math.min(w, lon);
			e = Math.max(e, lon);
			s = Math.min(s, lat);
			n = Math.max(n, lat);
		}
		const bounds: LngLatBoundsLike = [
			[w, s],
			[e, n]
		];
		// Extra room at the top and bottom for the toolbar and legend drawn over the map.
		map.fitBounds(bounds, {
			padding: { top: 64, bottom: 64, left: 40, right: 56 },
			maxZoom: 17,
			duration: 600
		});
	}

	/** Frame every device currently on the map. */
	export function fitDevices() {
		fitTo(devices.filter((d) => d.lat || d.lon).map((d) => [d.lon, d.lat]));
	}

	onMount(() => {
		let disposed = false;
		let observer: ResizeObserver | undefined;
		(async () => {
			const maplibregl = await import('maplibre-gl');
			// MapLibre 6 finds its worker next to its own module, which bundling moves; point it
			// at a worker Vite has bundled (with its shared chunk) instead.
			const { default: workerUrl } =
				await import('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url');
			maplibregl.setWorkerUrl(workerUrl);
			if (disposed) return;
			const m = new maplibregl.Map({
				container,
				style,
				center: [-98.5, 39.8],
				zoom: 3,
				attributionControl: { compact: true },
				dragRotate: false,
				pitchWithRotate: false
			});
			m.touchZoomRotate.disableRotation();
			m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
			// Added after the zoom buttons, so it stacks above them.
			m.addControl(
				{
					onAdd() {
						const group = document.createElement('div');
						group.className = 'maplibregl-ctrl maplibregl-ctrl-group';
						const button = document.createElement('button');
						button.type = 'button';
						button.className = 'fit-all';
						button.title = 'Recenter on all shown devices';
						button.setAttribute('aria-label', 'Recenter on all shown devices');
						button.innerHTML =
							'<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/><circle cx="12" cy="12" r="2.5"/></svg>';
						button.addEventListener('click', () => fitDevices());
						group.append(button);
						return group;
					},
					onRemove() {}
				},
				'bottom-right'
			);
			m.addControl(new maplibregl.ScaleControl({ unit: 'imperial' }), 'bottom-left');

			let popup: Popup | null = null;
			const interactive = ['devices', 'cluster-points'];
			for (const layer of interactive) {
				m.on('mousemove', layer, (e: MapLayerMouseEvent) => {
					m.getCanvas().style.cursor = 'pointer';
					const features = e.features ?? [];
					if (!features.length) return;
					popup ??= new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 });
					popup!.setLngLat(e.lngLat).setHTML(popupHtml(features)).addTo(m);
				});
				m.on('mouseleave', layer, () => {
					m.getCanvas().style.cursor = '';
					popup?.remove();
				});
				m.on('click', layer, (e: MapLayerMouseEvent) => {
					const id = e.features?.[0]?.properties?.id;
					if (id) onselect(String(id));
				});
			}
			m.on('mouseenter', 'cluster-circles', () => (m.getCanvas().style.cursor = 'pointer'));
			m.on('mouseleave', 'cluster-circles', () => (m.getCanvas().style.cursor = ''));
			m.on('click', 'cluster-circles', async (e: MapLayerMouseEvent) => {
				const feature = e.features?.[0];
				if (!feature) return;
				const source = m.getSource('clusters') as GeoJSONSource;
				const zoom = await source.getClusterExpansionZoom(feature.properties.cluster_id);
				const [lon, lat] = (feature.geometry as { coordinates: [number, number] }).coordinates;
				m.easeTo({ center: [lon, lat], zoom });
			});
			const reportBounds = () => {
				const b = m.getBounds();
				onbounds([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
			};
			m.on('moveend', reportBounds);
			m.on('load', () => {
				addLayers(m);
				ready = true;
				reportBounds();
			});
			observer = new ResizeObserver(() => m.resize());
			observer.observe(container);
			map = m;
		})();
		return () => {
			disposed = true;
			observer?.disconnect();
			map?.remove();
			map = null;
		};
	});

	function setVisible(m: MapLibreMap, layer: string, visible: boolean) {
		m.setLayoutProperty(layer, 'visibility', visible ? 'visible' : 'none');
	}

	$effect(() => {
		if (!map || !ready) return;
		for (const [name, layers] of Object.entries(BASEMAP_LAYERS)) {
			for (const layer of layers) setVisible(map, layer, name === basemap);
		}
	});

	$effect(() => {
		if (!map || !ready) return;
		const data = deviceFeatures(devices);
		(map.getSource('devices') as GeoJSONSource).setData(data);
		(map.getSource('clusters') as GeoJSONSource).setData(data);
	});

	$effect(() => {
		if (!map || !ready) return;
		setVisible(map, 'devices', !cluster);
		for (const layer of ['cluster-circles', 'cluster-count', 'cluster-points']) {
			setVisible(map, layer, cluster);
		}
		setVisible(map, 'heat', heat);
	});

	$effect(() => {
		if (!map || !ready) return;
		(map.getSource('tracks') as GeoJSONSource).setData({
			type: 'FeatureCollection',
			features: tracks.map((t) => ({
				type: 'Feature',
				geometry: { type: 'LineString', coordinates: t.points },
				properties: { session: t.session }
			}))
		});
		setVisible(map, 'tracks', showTracks);
		setVisible(map, 'tracks-casing', showTracks);
	});

	$effect(() => {
		if (!map || !ready) return;
		const selected = selectedId ? devices.find((d) => d.id === selectedId) : undefined;
		(map.getSource('selected') as GeoJSONSource).setData(
			selected && (selected.lat || selected.lon)
				? {
						type: 'FeatureCollection',
						features: [
							{
								type: 'Feature',
								geometry: { type: 'Point', coordinates: [selected.lon, selected.lat] },
								properties: {}
							}
						]
					}
				: EMPTY
		);
	});

	$effect(() => {
		if (!map || !ready) return;
		(map.getSource('sightings') as GeoJSONSource).setData({
			type: 'FeatureCollection',
			features: sightings.map((s) => ({
				type: 'Feature',
				geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
				properties: { signal: s.signal }
			}))
		});
	});

	let lastFit: string | null = null;
	$effect(() => {
		const key = fitKey;
		if (!map || !ready || !key || key === lastFit) return;
		const points = devices
			.filter((d) => d.lat || d.lon)
			.map((d): [number, number] => [d.lon, d.lat]);
		if (!points.length) return; // nothing matches: leave the view where it is
		// The first fit is immediate; later ones wait for typing or slider dragging to settle.
		const timer = setTimeout(
			() => {
				lastFit = key;
				fitTo(points);
			},
			lastFit === null ? 0 : 450
		);
		return () => clearTimeout(timer);
	});
</script>

<div class="map" bind:this={container}></div>

<style>
	.map {
		position: absolute;
		inset: 0;
	}

	.map :global(.fit-all) {
		display: flex;
		align-items: center;
		justify-content: center;
		color: var(--text);
	}

	.map :global(.pop) {
		display: grid;
		gap: 1px;
		max-width: 240px;
		font-size: 13px;
	}

	.map :global(.pop-sub) {
		color: var(--text-muted);
	}

	.map :global(.pop-mac) {
		color: var(--text-faint);
		font-family: var(--font-mono);
		font-size: 11.5px;
	}

	.map :global(.pop-more) {
		margin-top: 2px;
		color: var(--text-faint);
		font-size: 11.5px;
	}
</style>
