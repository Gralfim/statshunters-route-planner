const map = L.map('map').setView([50.0755, 14.4378], 11);

L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

const layerConfig = {
  visited: {
    url: '/api/tiles',
    style: { color: '#1f6feb', weight: 1, fillColor: '#58a6ff', fillOpacity: 0.28 }
  },
  frontier: {
    url: '/api/frontier',
    style: { color: '#d97706', weight: 1, fillColor: '#f59e0b', fillOpacity: 0.32 }
  },
  cluster: {
    url: '/api/cluster',
    style: { color: '#047857', weight: 1, fillColor: '#10b981', fillOpacity: 0.38 }
  },
  square: {
    url: '/api/square',
    style: { color: '#be123c', weight: 1, fillColor: '#fb7185', fillOpacity: 0.42 }
  }
};

const layers = {};

function popupText(feature) {
  const p = feature.properties;
  const visits = p.visit_count ? `<br>Visits: ${p.visit_count}` : '';
  const dates = p.first_visit ? `<br>${p.first_visit} - ${p.last_visit}` : '';
  return `Tile ${p.x}, ${p.y}${visits}${dates}`;
}

async function loadLayer(name) {
  if (layers[name]) return layers[name];

  const cfg = layerConfig[name];
  const data = await fetch(cfg.url).then(r => r.json());
  layers[name] = L.geoJSON(data, {
    style: cfg.style,
    onEachFeature: (feature, layer) => layer.bindPopup(popupText(feature))
  });

  return layers[name];
}

async function toggleLayer(name, checked) {
  const layer = await loadLayer(name);
  if (checked) {
    layer.addTo(map);
    if (name === 'visited') map.fitBounds(layer.getBounds(), { padding: [24, 24] });
  } else {
    map.removeLayer(layer);
  }
}

async function loadSummary() {
  const summary = await fetch('/api/summary').then(r => r.json());
  const stats = document.querySelector('#stats');
  stats.innerHTML = [
    ['Run tiles', summary.run_tiles],
    ['Frontier', summary.frontier_tiles],
    ['Cluster', summary.largest_cluster],
    ['Square', `${summary.largest_square} x ${summary.largest_square}`]
  ].map(([label, value]) => `
    <div class="stat">
      <div class="label">${label}</div>
      <div class="value">${value}</div>
    </div>
  `).join('');

  map.setView([summary.home.lat, summary.home.lon], 11);
}

document.querySelectorAll('[data-layer]').forEach(input => {
  input.addEventListener('change', event => {
    toggleLayer(event.target.dataset.layer, event.target.checked);
  });
});

loadSummary();
toggleLayer('visited', true);
toggleLayer('frontier', true);
