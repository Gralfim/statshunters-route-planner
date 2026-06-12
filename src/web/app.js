const map = L.map('map').setView([50.0755, 14.4378], 11);

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

const periodOrder = ['all', 'year', 'recent'];
const overlayGroups = {
  tiles: L.layerGroup().addTo(map),
  cluster: L.layerGroup().addTo(map),
  square: L.layerGroup().addTo(map)
};
const layerCache = {};
let periods = [];

function popupText(feature) {
  const p = feature.properties;
  if (p.kind) {
    return `${p.period} ${p.kind}<br>Size: ${p.size}`;
  }

  const visits = p.visit_count ? `<br>Visits: ${p.visit_count}` : '';
  const dates = p.first_visit ? `<br>${p.first_visit} - ${p.last_visit}` : '';
  return `Tile ${p.x}, ${p.y}${visits}${dates}`;
}

function layerStyle(period, overlay) {
  if (overlay === 'tiles') {
    return {
      color: period.color,
      weight: 0.5,
      fillColor: period.color,
      fillOpacity: 0.24,
      opacity: 0.22
    };
  }

  return {
    color: period.color,
    weight: overlay === 'square' ? 3 : 2,
    fillOpacity: 0,
    opacity: overlay === 'square' ? 0.95 : 0.75,
    dashArray: overlay === 'square' ? null : '5 4'
  };
}

async function loadLayer(periodKey, overlay) {
  const cacheKey = `${periodKey}:${overlay}`;
  if (layerCache[cacheKey]) return layerCache[cacheKey];

  const period = periods.find(p => p.key === periodKey);
  const data = await fetch(`/api/periods/${periodKey}/${overlay}`).then(r => r.json());
  layerCache[cacheKey] = L.geoJSON(data, {
    style: layerStyle(period, overlay),
    onEachFeature: (feature, layer) => layer.bindPopup(popupText(feature))
  });

  return layerCache[cacheKey];
}

async function drawOverlay(overlay) {
  overlayGroups[overlay].clearLayers();

  for (const periodKey of periodOrder) {
    const layer = await loadLayer(periodKey, overlay);
    overlayGroups[overlay].addLayer(layer);
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

async function drawCheckedOverlays() {
  for (const overlay of Object.keys(overlayGroups)) {
    const input = document.querySelector(`[data-overlay="${overlay}"]`);
    if (input.checked) await drawOverlay(overlay);
  }
}

async function fitToAllTiles(summary) {
  const allTiles = await loadLayer('all', 'tiles');
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
  await drawCheckedOverlays();
  await fitToAllTiles(summary);
}

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
