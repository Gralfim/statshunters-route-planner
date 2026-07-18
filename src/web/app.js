const map = L.map('map').setView([50.0755, 14.4378], 11);

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

// Poradi panes ridi, ktera vrstva dostane kliknuti: doporuceni lezi nad tiles,
// obrysy cluster/square jsou jen dekorace (interactive: false).
const PANES = { tiles: 410, opportunities: 420, cluster: 430, square: 440, route: 450 };
for (const [name, zIndex] of Object.entries(PANES)) {
  map.createPane(`${name}Pane`).style.zIndex = zIndex;
}

const OPPORTUNITY_COLORS = {
  unvisited: '#4a3aa7',
  visited: '#1baf7a'
};

const periodOrder = ['all', 'year', 'recent'];
const overlayGroups = {
  tiles: L.layerGroup().addTo(map),
  opportunities: L.layerGroup().addTo(map),
  cluster: L.layerGroup().addTo(map),
  square: L.layerGroup().addTo(map)
};
const layerCache = {};
let periods = [];

function opportunityPopup(p) {
  const visited = p.visited_periods || {};
  const visitStatus = [
    `Celkem: ${visited.all ? 'ano' : 'ne'}`,
    `Letos: ${visited.year ? 'ano' : 'ne'}`,
    `3 mesice: ${visited.recent ? 'ano' : 'ne'}`
  ].join('<br>');
  const reasons = (p.reasons || [])
    .map(reason => {
      const gain = reason.gain && reason.gain > 1 ? ` (+${reason.gain})` : '';
      return `${reason.priority}. ${reason.label}${gain}`;
    })
    .join('<br>');
  const lastVisit = p.last_visit
    ? `Naposledy: ${p.last_visit} (pred ${p.days_since_visit} dny)`
    : 'Nikdy nenavstiveno';
  return `#${p.rank} tile ${p.x}, ${p.y}<br>${p.top_reason}<br>Score: ${p.score}<br>${lastVisit}<br><br>${visitStatus}<br><br>${reasons}`;
}

function tilePopup(p) {
  const visits = p.visit_count ? `<br>Visits: ${p.visit_count}` : '';
  const dates = p.first_visit ? `<br>${p.first_visit} - ${p.last_visit}` : '';
  return `Tile ${p.x}, ${p.y}${visits}${dates}`;
}

function opportunityStyle(feature) {
  const priority = feature.properties.priority;
  const visited = feature.properties.visited_periods || {};
  const fillOpacity = Math.max(0.22, 0.7 - (priority - 1) * 0.06);
  const fillColor = visited.all ? OPPORTUNITY_COLORS.visited : OPPORTUNITY_COLORS.unvisited;

  return {
    color: '#111827',
    weight: priority <= 3 ? 1.5 : 0.8,
    fillColor,
    fillOpacity,
    opacity: priority <= 3 ? 0.85 : 0.45
  };
}

// Kazdy tile jednou, barvou obdobi posledni navstevy (misto tri vrstev pres sebe,
// ktere se micaly do nerozlisitelnych odstinu).
async function loadTilesLayer() {
  if (layerCache.tiles) return layerCache.tiles;

  const collections = await Promise.all(
    periodOrder.map(key => fetch(`/api/periods/${key}/tiles`).then(r => r.json()))
  );
  const tileClass = {};
  periodOrder.forEach((key, index) => {
    for (const feature of collections[index].features) {
      tileClass[`${feature.properties.x}:${feature.properties.y}`] = key;
    }
  });

  layerCache.tiles = L.geoJSON(collections[0], {
    pane: 'tilesPane',
    style: feature => {
      const key = tileClass[`${feature.properties.x}:${feature.properties.y}`];
      const period = periods.find(item => item.key === key);
      return {
        color: period.color,
        weight: 0.5,
        opacity: 0.45,
        fillColor: period.color,
        fillOpacity: 0.32
      };
    },
    onEachFeature: (feature, layer) => layer.bindPopup(tilePopup(feature.properties))
  });

  return layerCache.tiles;
}

async function loadOpportunityLayer() {
  if (layerCache.opportunities) return layerCache.opportunities;

  const data = await fetch('/api/opportunities').then(r => r.json());
  layerCache.opportunities = L.geoJSON(data, {
    pane: 'opportunitiesPane',
    style: opportunityStyle,
    onEachFeature: (feature, layer) => layer.bindPopup(opportunityPopup(feature.properties))
  });

  return layerCache.opportunities;
}

async function loadOutlineLayer(periodKey, overlay) {
  const cacheKey = `${periodKey}:${overlay}`;
  if (layerCache[cacheKey]) return layerCache[cacheKey];

  const period = periods.find(item => item.key === periodKey);
  const data = await fetch(`/api/periods/${periodKey}/${overlay}`).then(r => r.json());
  layerCache[cacheKey] = L.geoJSON(data, {
    pane: `${overlay}Pane`,
    interactive: false,
    style: {
      color: period.color,
      weight: overlay === 'square' ? 3 : 2,
      fill: false,
      opacity: overlay === 'square' ? 0.95 : 0.75,
      dashArray: overlay === 'square' ? null : '5 4'
    }
  });

  return layerCache[cacheKey];
}

async function drawOverlay(overlay) {
  overlayGroups[overlay].clearLayers();

  if (overlay === 'tiles') {
    overlayGroups.tiles.addLayer(await loadTilesLayer());
    return;
  }

  if (overlay === 'opportunities') {
    overlayGroups.opportunities.addLayer(await loadOpportunityLayer());
    return;
  }

  for (const periodKey of periodOrder) {
    overlayGroups[overlay].addLayer(await loadOutlineLayer(periodKey, overlay));
  }
}

function renderStats() {
  const stats = document.querySelector('#stats');
  stats.innerHTML = periods.map(period => `
    <section class="period">
      <header><span class="swatch" style="background:${period.color}"></span>${period.label}</header>
      <div class="metrics">
        <div>
          <div class="label">Tiles</div>
          <div class="value">${period.run_tiles}</div>
        </div>
        <div>
          <div class="label">Cluster</div>
          <div class="value">${period.largest_cluster}</div>
        </div>
        <div>
          <div class="label">Square</div>
          <div class="value">${period.largest_square}x</div>
        </div>
      </div>
      <div class="label">${period.start_date || 'zacatek'} - ${period.end_date}</div>
    </section>
  `).join('');
}

function renderLegend() {
  document.querySelector('#legend').innerHTML = `
    <div>Barva tile = obdobi posledni navstevy</div>
    <div><span class="swatch" style="background:${OPPORTUNITY_COLORS.unvisited}"></span> Doporuceni: nikdy nenavstiveno</div>
    <div><span class="swatch" style="background:${OPPORTUNITY_COLORS.visited}"></span> Doporuceni: navrat po case</div>
  `;
}

async function drawCheckedOverlays() {
  for (const overlay of Object.keys(overlayGroups)) {
    const input = document.querySelector(`[data-overlay="${overlay}"]`);
    if (input.checked) await drawOverlay(overlay);
  }
}

async function fitToAllTiles(summary) {
  const allTiles = await loadTilesLayer();
  if (allTiles.getBounds().isValid()) {
    map.fitBounds(allTiles.getBounds(), { padding: [24, 24] });
  } else {
    map.setView([summary.home.lat, summary.home.lon], 11);
  }
}

async function loadSummary() {
  const summary = await fetch('/api/summary').then(r => r.json());
  periods = periodOrder.map(key => summary.periods.find(period => period.key === key));

  renderStats();
  renderLegend();
  routeDistance.value = summary.target_distance_km;
  routeTolerance.value = summary.distance_tolerance_km;
  setStart(summary.home.lat, summary.home.lon);
  await drawCheckedOverlays();
  await fitToAllTiles(summary);
}

const routeDistance = document.querySelector('#route-distance');
const routeTolerance = document.querySelector('#route-tolerance');
const routeStartText = document.querySelector('#route-start');
const planButton = document.querySelector('#plan');
const gpxButton = document.querySelector('#gpx');
const routeStatus = document.querySelector('#route-status');
const routeBenefit = document.querySelector('#route-benefit');

const GAIN_LABELS = {
  all_square: 'square celkem',
  all_cluster: 'cluster celkem',
  all_unvisited: 'uplne nove tiles',
  year_square: 'square letos',
  year_cluster: 'cluster letos',
  year_unvisited: 'nove letos',
  recent_square: 'square 3 mes.',
  recent_cluster: 'cluster 3 mes.',
  recent_unvisited: 'nove za 3 mes.'
};

function renderBenefit(route) {
  const parts = Object.entries(route.benefit.gains)
    .filter(([, gain]) => gain > 0)
    .map(([key, gain]) => `+${gain} ${GAIN_LABELS[key] || key}`);
  routeBenefit.innerHTML = `<strong>Prinos trasy (score ${route.benefit.total})</strong><br>`
    + (parts.length ? parts.join('<br>') : 'Zadne zlepseni statistik')
    + `<br>stari navstev: +${route.benefit.staleness}`;
  routeBenefit.hidden = false;
}
const routeLayerGroup = L.layerGroup().addTo(map);
let startMarker = null;
let lastRoute = null;

function setStart(lat, lon) {
  if (!startMarker) {
    startMarker = L.marker([lat, lon], { draggable: true, title: 'Start trasy' }).addTo(map);
    startMarker.on('dragend', () => {
      const position = startMarker.getLatLng();
      setStart(position.lat, position.lng);
    });
  } else {
    startMarker.setLatLng([lat, lon]);
  }
  routeStartText.textContent = `Start: ${lat.toFixed(4)}, ${lon.toFixed(4)} (pretahni spendlik, nebo prave tlacitko v mape)`;
}

map.on('contextmenu', event => setStart(event.latlng.lat, event.latlng.lng));

function drawRoute(route) {
  routeLayerGroup.clearLayers();
  routeLayerGroup.addLayer(L.polyline(route.coordinates, {
    pane: 'routePane', color: '#ffffff', weight: 7, opacity: 0.9
  }));
  routeLayerGroup.addLayer(L.polyline(route.coordinates, {
    pane: 'routePane', color: '#111827', weight: 3.5, opacity: 0.95
  }));
  map.fitBounds(L.latLngBounds(route.coordinates), { padding: [40, 40] });
}

async function planRoute() {
  const position = startMarker.getLatLng();
  planButton.disabled = true;
  planButton.textContent = 'Planuji...';
  routeStatus.style.color = '#53606f';
  routeStatus.textContent = 'Porovnavam varianty trasy... v nove oblasti muze prvni vypocet trvat i minuty (stahovani pesi mapy).';
  gpxButton.hidden = true;
  routeBenefit.hidden = true;

  try {
    const response = await fetch('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat: position.lat,
        lon: position.lng,
        distance_km: Number(routeDistance.value),
        tolerance_km: Number(routeTolerance.value)
      })
    });
    const route = await response.json();
    if (!response.ok) throw new Error(route.detail || response.statusText);

    lastRoute = route;
    if (!route.waypoint_tiles.length) {
      routeLayerGroup.clearLayers();
      routeStatus.textContent = 'V dosahu startu nejsou zadne doporucene tiles - zkus jiny start nebo delsi trasu.';
      return;
    }

    drawRoute(route);
    const target = route.within_target ? '' : ' (mimo toleranci!)';
    routeStatus.textContent =
      `Trasa ${route.length_km} km${target}, nejlepsi z ${route.variants_compared} variant; ` +
      `protne ${route.tiles_crossed.length} tiles (z toho ${route.crossed_recommended} doporucenych).`;
    renderBenefit(route);
    gpxButton.hidden = false;
  } catch (error) {
    routeStatus.style.color = '#b91c1c';
    routeStatus.textContent = `Planovani selhalo: ${error.message}`;
  } finally {
    planButton.disabled = false;
    planButton.textContent = 'Naplanovat trasu';
  }
}

planButton.addEventListener('click', planRoute);

gpxButton.addEventListener('click', () => {
  if (!lastRoute) return;
  const blob = new Blob([lastRoute.gpx], { type: 'application/gpx+xml' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `trasa-${lastRoute.length_km}km.gpx`;
  link.click();
  URL.revokeObjectURL(link.href);
});

const syncButton = document.querySelector('#sync');
const syncStatus = document.querySelector('#sync-status');

syncButton.addEventListener('click', async () => {
  syncButton.disabled = true;
  syncButton.textContent = 'Stahuji data...';
  syncStatus.textContent = '';

  try {
    const response = await fetch('/api/sync', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || response.statusText);
    location.reload();
  } catch (error) {
    syncStatus.textContent = `Synchronizace selhala: ${error.message}`;
    syncButton.disabled = false;
    syncButton.textContent = 'Aktualizovat data ze StatsHunters';
  }
});

document.querySelectorAll('[data-overlay]').forEach(input => {
  input.addEventListener('change', async event => {
    const overlay = event.target.dataset.overlay;
    if (event.target.checked) {
      await drawOverlay(overlay);
    } else {
      overlayGroups[overlay].clearLayers();
    }
  });
});

loadSummary();
