const map = L.map('map').setView([50.0755, 14.4378], 11);

// OSM se pripoji hned, at mapa neni prazdna; turisticky podklad z Mapy.cz se
// dokresli, jakmile dorazi konfigurace (viz setupBasemap).
const osmLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
}).addTo(map);

// Mapsety Mapy.cz. `outdoor` kresli turisticke znacky a cyklotrasy - tedy to,
// podle ceho se trasa planuje; na OSM podkladu videt nejsou, proto je vychozi.
// Retina dlazdice (@2x) nabizi API jen u basic a outdoor.
const MAPY_MAPSETS = [
  { key: 'outdoor', label: 'Turisticka (Mapy.cz)', retina: true },
  { key: 'basic', label: 'Zakladni (Mapy.cz)', retina: true },
  { key: 'winter', label: 'Zimni (Mapy.cz)', retina: false },
  { key: 'aerial', label: 'Letecka (Mapy.cz)', retina: false }
];

function mapyTileLayer(config, mapset, retina, attribution) {
  const tileSize = retina && L.Browser.retina ? '256@2x' : '256';
  const url = config.tile_url
    .replace('{mapset}', mapset)
    .replace('{tile_size}', tileSize)
    .replace('{api_key}', encodeURIComponent(config.api_key));
  return L.tileLayer(url, { maxZoom: 19, attribution });
}

// Zobrazeni loga Mapy.com je podminka pouzivani jejich API, ne dekorace -
// pripina se a odepina spolu s jejich podkladem.
const mapyLogo = L.control({ position: 'bottomleft' });
mapyLogo.onAdd = function () {
  // leaflet-control je nutna: rohovy kontejner ma pointer-events:none, bez ni
  // by povinny odkaz na Mapy.com nesel kliknout
  const link = L.DomUtil.create('a', 'mapy-logo leaflet-control');
  link.href = 'https://mapy.com/';
  link.target = '_blank';
  link.rel = 'noopener';
  link.innerHTML = '<img src="https://api.mapy.cz/img/api/logo.svg" alt="Mapy.com">';
  L.DomEvent.disableClickPropagation(link);
  return link;
};

async function setupBasemap() {
  let config;
  try {
    config = await fetch('/api/basemap').then(response => response.json());
  } catch (error) {
    return;  // podklad zustane na OSM
  }
  if (config.provider !== 'mapy.cz' || !config.api_key) return;

  // Neplatny nebo vycerpany klic by jinak skoncil prazdnou mapou bez vysvetleni
  // (API vraci 403). Par chyb muze byt vypadek site, proto az opakovane.
  const baseLayers = {};
  let tileErrors = 0;

  function fallbackToOsm() {
    if (map.hasLayer(osmLayer)) return;
    console.warn('Dlazdice Mapy.cz se nedari nacist (neplatny klic nebo vycerpana kvota?)'
      + ' - prepinam na OpenStreetMap.');
    for (const layer of Object.values(baseLayers)) {
      if (layer !== osmLayer) map.removeLayer(layer);
    }
    osmLayer.addTo(map);
    mapyLogo.remove();
  }

  for (const mapset of MAPY_MAPSETS) {
    const tiles = mapyTileLayer(config, mapset.key, mapset.retina, config.attribution);
    tiles.on('tileerror', () => {
      if (++tileErrors >= 6) fallbackToOsm();
    });
    // letecka bez popisu je k orientaci nepouzitelna - prilep na ni nazvy
    baseLayers[mapset.label] = mapset.key === 'aerial'
      ? L.layerGroup([tiles, mapyTileLayer(config, 'names-overlay', false, config.attribution)])
      : tiles;
  }
  baseLayers['OpenStreetMap'] = osmLayer;

  // nejdriv pripnout novy podklad, teprve pak odebrat stary - bez probliknuti
  const tourist = baseLayers[MAPY_MAPSETS[0].label];
  tourist.addTo(map);
  map.removeLayer(osmLayer);
  mapyLogo.addTo(map);

  L.control.layers(baseLayers, null, { position: 'topleft' }).addTo(map);
  map.on('baselayerchange', event => {
    if (event.layer === osmLayer) {
      mapyLogo.remove();
    } else {
      mapyLogo.addTo(map);
    }
  });
}

setupBasemap();

// Poradi panes ridi, ktera vrstva dostane kliknuti: doporuceni lezi nad tiles,
// obrysy cluster/square jsou jen dekorace (interactive: false).
// metro lezi pod dlazdicemi: je to orientacni podklad, nema prekryvat data
const PANES = {
  metro: 405, tiles: 410, opportunities: 420, cluster: 430, square: 440,
  route: 450, poi: 460  // body nahore, at jdou najet mysi
};
for (const [name, zIndex] of Object.entries(PANES)) {
  map.createPane(`${name}Pane`).style.zIndex = zIndex;
}

const OPPORTUNITY_COLOR = '#4a3aa7';

const periodOrder = ['all', 'year', 'recent'];
const overlayGroups = {
  metro: L.layerGroup().addTo(map),
  tiles: L.layerGroup().addTo(map),
  opportunities: L.layerGroup().addTo(map),
  cluster: L.layerGroup().addTo(map),
  square: L.layerGroup().addTo(map),
  pois: L.layerGroup().addTo(map)
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
  const weight = priority <= 3 ? 1.5 : 0.8;
  const opacity = priority <= 3 ? 0.85 : 0.45;

  // Uz navstiveny tile nese barvu obdobi posledni navstevy - vypln doporuceni
  // by ji jen prekryla a nic navic nerekne, zustava proto jen obrys.
  if (visited.all) {
    return { color: '#111827', weight, opacity, fill: false };
  }

  return {
    color: '#111827',
    weight,
    fillColor: OPPORTUNITY_COLOR,
    fillOpacity: Math.max(0.22, 0.7 - (priority - 1) * 0.06),
    opacity
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

// Metro jako orientacni podklad: turisticka mapa Mapy.cz ho nekresli a bez nej
// se v mape spatne hleda. Data uz mame ze site PID (kvuli vypravam).
async function loadMetroLayer() {
  if (layerCache.metro) return layerCache.metro;

  const data = await fetch('/api/transit/metro').then(r => r.json());
  const group = L.layerGroup();

  for (const line of data.lines) {
    for (const segment of line.segments) {
      // bila podkresba, aby linka drzela i na barevne turisticke mape
      group.addLayer(L.polyline(segment, {
        pane: 'metroPane', color: '#ffffff', weight: 7, opacity: 0.55, interactive: false
      }));
      group.addLayer(L.polyline(segment, {
        pane: 'metroPane', color: line.color, weight: 3.5, opacity: 0.9, interactive: false
      }));
    }
  }

  for (const station of data.stations) {
    const colors = station.lines.map(name => (data.lines.find(l => l.line === name) || {}).color);
    group.addLayer(L.circleMarker([station.lat, station.lon], {
      pane: 'metroPane',
      radius: station.lines.length > 1 ? 5 : 4,
      color: '#111827',
      weight: 1.5,
      // prestupni stanice bile, jinak barva sve linky
      fillColor: station.lines.length > 1 ? '#ffffff' : (colors[0] || '#53606f'),
      fillOpacity: 1
    }).bindTooltip(`${station.name} (${station.lines.join('+')})`));
  }

  layerCache.metro = group;
  return group;
}

// Orientacni body a obcerstveni. Mapy.cz je v dlazdicich nekresli a jejich API
// je nema, takze pochazeji z OSM. Postupne objevovani podle dulezitosti (jako
// v aplikaci Mapy.cz) delame sami: kazdy bod nese min_zoom a pri oddaleni se
// schova - jinak by Praha byla jedna kupa ikon.
let poiPoints = null;

function poiMarker(point) {
  return L.marker([point.lat, point.lon], {
    pane: 'poiPane',
    icon: L.divIcon({
      className: 'poi-icon',
      html: `<span title="${point.label}">${point.icon}</span>`,
      iconSize: [18, 18],
      iconAnchor: [9, 9]
    })
  }).bindTooltip(`${point.name} (${point.label})`);
}

function renderPois() {
  const group = overlayGroups.pois;
  const hint = document.querySelector('#poi-hint');
  group.clearLayers();
  if (!poiPoints) return;

  const zoom = map.getZoom();
  const bounds = map.getBounds().pad(0.2);
  const shown = {};
  let hidden = 0;

  for (const point of poiPoints) {
    if (!bounds.contains([point.lat, point.lon])) continue;
    if (point.min_zoom > zoom) {
      hidden += 1;
      continue;
    }
    shown[point.label] = (shown[point.label] || 0) + 1;
    group.addLayer(poiMarker(point));
  }

  // Bez tohoto vypisu neni poznat, ze se cast bodu skryva podle priblizeni -
  // uzivatel jen vidi, ze restaurace "nikde nejsou".
  if (!hint) return;
  const parts = Object.entries(shown).sort((a, b) => b[1] - a[1])
    .map(([label, count]) => `${count} ${label}`);
  hint.textContent = parts.length
    ? parts.join(', ') + (hidden ? ` · ${hidden} dalsich az po priblizeni` : '')
    : (hidden ? `${hidden} bodu se zobrazi az po priblizeni` : 'v tomto vyrezu nic');
}

async function loadPoiLayer() {
  if (!poiPoints) {
    const data = await fetch('/api/pois').then(r => r.json());
    poiPoints = data.points || [];
  }
  renderPois();
}

// prekresluje se pri pohybu mapy, ale jen kdyz je vrstva zapnuta
map.on('moveend zoomend', () => {
  const input = document.querySelector('[data-overlay="pois"]');
  if (input && input.checked) renderPois();
});

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

  if (overlay === 'metro') {
    overlayGroups.metro.addLayer(await loadMetroLayer());
    return;
  }

  if (overlay === 'pois') {
    await loadPoiLayer();
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
    <div><span class="swatch" style="background:${OPPORTUNITY_COLOR}"></span> Doporuceni: nikdy nenavstiveno</div>
  `;
}

async function drawCheckedOverlays(skip = []) {
  for (const overlay of Object.keys(overlayGroups)) {
    if (skip.includes(overlay)) continue;
    const input = document.querySelector(`[data-overlay="${overlay}"]`);
    if (input.checked) await drawOverlay(overlay);
  }
}

// Uvodni pohled kryje okoli domova, ne vsechny tiles: zamorske aktivity
// (dovolene) by jinak roztahly meritko na cely svet.
const HOME_VIEW_RADIUS_M = 60000;

async function fitToHomeArea(summary) {
  const tilesLayer = await loadTilesLayer();
  const home = L.latLng(summary.home.lat, summary.home.lon);
  const bounds = L.latLngBounds([]);

  tilesLayer.eachLayer(layer => {
    const tileBounds = layer.getBounds();
    if (home.distanceTo(tileBounds.getCenter()) <= HOME_VIEW_RADIUS_M) {
      bounds.extend(tileBounds);
    }
  });

  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [24, 24] });
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
  routeBudget.value = summary.expedition_budget_min;
  routePace.value = summary.run_pace_min_per_km;
  routeQuiet.value = summary.quiet_weight;
  renderQuietLabel();
  setStart(summary.home.lat, summary.home.lon);

  // Meritko se nastavi hned podle (rychle) vrstvy tiles; teprve pak se dokresluji
  // pomalejsi vrstvy, aby uz mapa pod rukama neposkocila.
  const tilesShown = document.querySelector('[data-overlay="tiles"]').checked;
  if (tilesShown) await drawOverlay('tiles');
  await fitToHomeArea(summary);
  await drawCheckedOverlays(tilesShown ? ['tiles'] : []);
}

const routeDistance = document.querySelector('#route-distance');
const routeTolerance = document.querySelector('#route-tolerance');
const routeBudget = document.querySelector('#route-budget');
const routePace = document.querySelector('#route-pace');
const routeQuiet = document.querySelector('#route-quiet');
const routeQuietLabel = document.querySelector('#route-quiet-label');
const routeWeekend = document.querySelector('#route-weekend');
routeWeekend.checked = [0, 6].includes(new Date().getDay());

// Posuvnik prinos <-> klid: jak silne se do vyberu trasy pocita podil delky
// vedouci podel vyznamnych ulic.
function describeQuiet(value) {
  if (value <= 0) return 'jen sber dlazdic';
  if (value < 0.35) return 'prevazne sber dlazdic';
  if (value < 0.75) return 'vyvazene';
  if (value < 1) return 'prevazne klid';
  return 'klid za kazdou cenu';
}

function renderQuietLabel() {
  routeQuietLabel.textContent = describeQuiet(Number(routeQuiet.value));
}

routeQuiet.addEventListener('input', renderQuietLabel);
const routeStartText = document.querySelector('#route-start');
const planButton = document.querySelector('#plan');
const planExpeditionButton = document.querySelector('#plan-expedition');
const gpxButton = document.querySelector('#gpx');
const routeStatus = document.querySelector('#route-status');
const routeBenefit = document.querySelector('#route-benefit');
const routeSegments = document.querySelector('#route-segments');
const routeDirections = document.querySelector('#route-directions');

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

// Zvyrazneni useku v mape pri kliknuti na radek itinerare. Souradnice kroku se
// neposilaji - dopocitaji se z kumulativni vzdalenosti po trase, kterou uz
// pocita odecet vzdalenosti (attachDistanceProbe).
const highlightLayer = L.layerGroup().addTo(map);
let routeCumulative = null;
let highlightedStep = null;

function coordinateAt(km) {
  if (!routeCumulative) return null;
  const target = km * 1000;
  let best = 0;
  for (let i = 1; i < routeCumulative.length; i++) {
    if (Math.abs(routeCumulative[i] - target) < Math.abs(routeCumulative[best] - target)) best = i;
  }
  return best;
}

function clearHighlight() {
  highlightLayer.clearLayers();
  highlightedStep = null;
  for (const row of routeDirections.querySelectorAll('li')) {
    row.classList.remove('active');
  }
}

function highlightStep(index, step, row) {
  if (highlightedStep === index) {   // druhy klik zvyrazneni zrusi
    clearHighlight();
    return;
  }
  clearHighlight();
  highlightedStep = index;
  row.classList.add('active');

  const from = coordinateAt(step.at_km);
  const to = coordinateAt(step.at_km + step.km);
  if (from === null || to === null || to <= from) return;

  const part = lastRoute.coordinates.slice(from, to + 1);
  highlightLayer.addLayer(L.polyline(part, {
    pane: 'routePane', color: '#e34948', weight: 8, opacity: 0.85
  }));
  map.fitBounds(L.latLngBounds(part), { padding: [60, 60], maxZoom: 17 });
}

// Tahak na trasu: useky s nazvy ulic/cest, delkou a smerem zatoceni.
function renderDirections(route) {
  const steps = (route && route.directions) || [];
  clearHighlight();
  if (!steps.length) {
    routeDirections.hidden = true;
    return;
  }

  // Cas se uvadi KUMULATIVNE stejne jako kilometraz - v jedne stupnici se cte
  // lip nez smes "od startu" a "na tomhle useku".
  const pace = Number(routePace.value) || 6;

  const rows = steps.map(step => {
    const turn = step.turn ? `<span class="turn">${step.turn}</span> ` : '';
    const steps_note = step.steps ? ' po schodech' : '';
    const bridge = step.bridge ? ' (most)' : '';
    const heading = step.start_heading ? ` <span class="dist">smer ${step.start_heading}</span>` : '';
    // Znacka bez rozsahu vede po celem useku; s rozsahem jen po jeho casti -
    // bez toho by bezec sledoval znacky i tam, kde uz odbocuji jinam.
    const trail = step.trail
      ? ` <span class="trail">${step.trail}${step.trail_km
          ? ` jen ${step.trail_km[0].toFixed(1)}-${step.trail_km[1].toFixed(1)} km` : ''}</span>`
      : '';
    // ulice pod znackou - popis nese znacka, jmena jsou jen poznamka
    const via = (step.via && step.via.length)
      ? ` <span class="via">(${step.via.join(', ')})</span>` : '';
    const at = `<span class="dist">${step.at_km.toFixed(1)} km</span>`
      + `<span class="mins">${Math.round(step.at_km * pace)} min</span>`;
    // rozhodovaci body uvnitr useku - beze zmeny nazvu cesty by zanikly
    const decisions = (step.decisions && step.decisions.length)
      ? `<div class="decision">${step.decisions.map(
          d => `${d.at_km.toFixed(1)} km ${d.turn}`).join(' · ')}</div>`
      : '';
    const cross = (step.crossings && step.crossings.length)
      ? `<div class="cross">${step.crossings.map(c => `${c.name} (${c.at_km.toFixed(1)} km)`).join(', ')}</div>`
      : '';
    const forks = (step.forks && step.forks.length)
      ? `<div class="fork">${step.forks.map(f => `${f.at_km.toFixed(2)} km drz se ${f.keep}`).join(' · ')}</div>`
      : '';
    // Kvuli cemu se cely beh dela: kde a jak hluboko se sbira dlazdice.
    // Melka navsteva je riziko - pri chybe GPS se nemusi zapocitat.
    const tiles = (step.tiles && step.tiles.length)
      ? `<div class="tile">${step.tiles.map(t => {
          const goal = t.waypoint ? 'CIL ' : '';
          const risk = t.depth_m < 75 ? ' <span class="shallow">jen tesne!</span>' : '';
          return `${goal}${t.tile[0]},${t.tile[1]} od ${t.at_km.toFixed(1)} km`
            + ` (${t.km.toFixed(1)} km uvnitr, ${t.depth_m} m od hranice)${risk}`;
        }).join('<br>')}</div>`
      : '';
    return `<li>${at} ${turn}${step.label}${trail}${steps_note}${bridge}${heading}`
      + `${decisions}${via}${tiles}${cross}${forks}</li>`;
  }).join('');

  const collected = steps.reduce((sum, step) => sum + (step.tiles || []).length, 0);
  const picked = collected ? `, sbira ${collected} dlazdic` : '';
  routeDirections.innerHTML =
    `<details open><summary>Itinerar behu (${steps.length} useku, ${route.length_km} km${picked})`
    + `</summary><ul class="itinerary">${rows}</ul></details>`;
  routeDirections.hidden = false;

  // klik na radek zvyrazni odpovidajici usek v mape
  routeDirections.querySelectorAll('li').forEach((row, index) => {
    row.addEventListener('click', () => highlightStep(index, steps[index], row));
  });
}

// Vyber z nekolika dobrych variant. Plánovac uz je porovnal, ale posledni slovo
// ma uzivatel - podle pocasi a chuti (v horku radeji podel vody a ve stinu).
// Varianty se nehodnoti, jen popisuji; rozhodnuti je na cloveku.
const routeVariants = document.querySelector('#route-variants');

function variantSummary(option, index) {
  const route = option.route || option;          // vyprava nese trasu uvnitr
  const parts = [];
  let title = index === 0 ? 'Doporucena' : `Varianta ${index + 1}`;

  if (option.kind) {                             // vyprava, ne holy okruh
    title += option.kind === 'transit' ? `: ${option.alight.name}` : ': bez MHD';
    parts.push(`${option.run_km} km behu`, `${Math.round(option.total_min)} min`);
    if (option.kind === 'transit' && !option.return_stop) parts.push('zpatky domu behem');
  } else {
    parts.push(`${route.length_km} km`);
  }

  parts.push(`prinos ${Math.round(route.benefit.total)}`);
  if (route.progress > 0) parts.push(`+${Math.round(route.progress)} k square`);
  parts.push(`${Math.round(route.trail_share * 100)} % po znackach`);
  parts.push(`${Math.round(route.along_major_share * 100)} % podel rusnych`);
  if (route.corridor_share > 0.02) {
    parts.push(`${Math.round(route.corridor_share * 100)} % stejnym koridorem`);
  }
  return `<span class="vtitle">${title}</span><span class="vmeta">${parts.join(' · ')}</span>`;
}

function renderVariants(options, onPick) {
  if (options.length < 2) {
    routeVariants.hidden = true;
    return;
  }
  routeVariants.innerHTML = '';
  options.forEach((option, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.innerHTML = variantSummary(option, index);
    button.setAttribute('aria-pressed', index === 0 ? 'true' : 'false');
    button.addEventListener('click', () => {
      for (const other of routeVariants.querySelectorAll('button')) {
        other.setAttribute('aria-pressed', 'false');
      }
      button.setAttribute('aria-pressed', 'true');
      onPick(option);
    });
    routeVariants.appendChild(button);
  });
  routeVariants.hidden = false;
}

function renderBenefit(route) {
  const parts = Object.entries(route.benefit.gains)
    .filter(([, gain]) => gain > 0)
    .map(([key, gain]) => `+${gain} ${GAIN_LABELS[key] || key}`);
  // Koridor, ne shoda ulic: trasa, ktera jde udolim tam po jedne strane a zpet
  // po druhe, ma opakovanych ulic nula a pritom je porad na tomtez miste.
  // Zapocitavaji se oba pruchody - "kolik behu bude na mistech, ktera uz znam".
  const repeated = route.corridor_km > 0
    ? `stejnym koridorem: ${route.corridor_km} km `
      + `(${Math.round(route.corridor_share * 100)} %`
      + (route.repeated_km > 0 ? `, z toho touz ulici ${route.repeated_km} km` : '')
      + ')'
    : 'nikde neopakuje koridor';

  // Merky, podle kterych se trasa vybrala - bez nich nejde posuvnik ladit
  // ani poznat, co zmena delky/klidu udelala.
  const major = route.along_major_km !== undefined
    ? `podel rusnych ulic: ${route.along_major_km} km `
      + `(${Math.round(route.along_major_share * 100)} %)`
    : '';
  const trail = route.trail_km !== undefined
    ? `po znacenych trasach: ${route.trail_km} km `
      + `(${Math.round(route.trail_share * 100)} %)`
    : '';
  const offBy = route.target_km !== undefined
    ? Math.abs(route.length_km - route.target_km)
    : null;
  const length = offBy === null ? ''
    : `delka: ${route.length_km} km (cil ${route.target_km}, odchylka `
      + `${offBy < 0.05 ? 'presne na cili' : offBy.toFixed(1) + ' km'})`;

  // Strategicky postup: dlazdice, ktere max square jeste nezvetsi, ale priblizi
  // ho k dalsimu behu. Bez toho cisla nepopisou hlavni duvod, proc se nekdy bezi.
  const progress = route.progress > 0
    ? `<br>krok k vetsimu square: +${Math.round(route.progress)}`
    : '';

  routeBenefit.innerHTML = `<strong>Prinos trasy (score ${route.benefit.total})</strong><br>`
    + (parts.length ? parts.join('<br>') : 'Zadne zlepseni statistik')
    + `<br>stari navstev: +${route.benefit.staleness}`
    + progress
    + `<hr class="thin">${[length, major, trail, repeated].filter(Boolean).join('<br>')}`
    + (route.score !== undefined ? `<br>vysledne skore: ${route.score}` : '');
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

// Odecet vzdalenosti od startu behu: pri najeti na trasu ukaze, kolik km je
// dany bod od zacatku (jako mapy.cz) - slouzi k presnemu popisu mist na trase.
const probeMarker = L.circleMarker([0, 0], {
  pane: 'routePane', radius: 6, color: '#111827', weight: 2,
  fillColor: '#ffffff', fillOpacity: 1, interactive: false
});

function attachDistanceProbe(layer, coordinates) {
  const cumulative = [0];
  for (let i = 1; i < coordinates.length; i++) {
    cumulative[i] = cumulative[i - 1]
      + L.latLng(coordinates[i - 1]).distanceTo(coordinates[i]);
  }
  const total = cumulative[cumulative.length - 1] / 1000;
  routeCumulative = cumulative;  // pouziva i zvyrazneni useku z itinerare

  layer.bindTooltip('', { sticky: true, direction: 'top', opacity: 0.95 });
  layer.on('mousemove', event => {
    // levna aproximace vzdalenosti (staci pro nalezeni nejblizsiho bodu)
    const { lat, lng } = event.latlng;
    const coslat = Math.cos(lat * Math.PI / 180);
    let best = 0;
    let bestDistance = Infinity;
    for (let i = 0; i < coordinates.length; i++) {
      const dy = coordinates[i][0] - lat;
      const dx = (coordinates[i][1] - lng) * coslat;
      const distance = dy * dy + dx * dx;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    }
    const km = cumulative[best] / 1000;
    layer.setTooltipContent(
      `${km.toFixed(2)} km od startu<br>(zbyva ${(total - km).toFixed(2)} km)`
    );
    probeMarker.setLatLng(coordinates[best]).addTo(map);
  });
  layer.on('mouseout', () => probeMarker.remove());
}

function drawRoute(route) {
  routeLayerGroup.clearLayers();
  probeMarker.remove();
  routeLayerGroup.addLayer(L.polyline(route.coordinates, {
    pane: 'routePane', color: '#ffffff', weight: 7, opacity: 0.9
  }));
  const line = L.polyline(route.coordinates, {
    pane: 'routePane', color: '#111827', weight: 3.5, opacity: 0.95
  });
  attachDistanceProbe(line, route.coordinates);
  routeLayerGroup.addLayer(line);
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
  routeVariants.hidden = true;
  routeSegments.hidden = true;
  routeDirections.hidden = true;

  try {
    const response = await fetch('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat: position.lat,
        lon: position.lng,
        distance_km: Number(routeDistance.value),
        tolerance_km: Number(routeTolerance.value),
        quiet_weight: Number(routeQuiet.value)
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

    function show(chosen) {
      lastRoute = chosen;
      drawRoute(chosen);
      const target = chosen.within_target ? '' : ' (mimo toleranci!)';
      routeStatus.textContent =
        `Trasa ${chosen.length_km} km${target}, nejlepsi z ${chosen.variants_compared} variant; ` +
        `protne ${chosen.tiles_crossed.length} tiles (z toho ${chosen.crossed_recommended} doporucenych).`;
      renderBenefit(chosen);
      renderDirections(chosen);
      gpxButton.hidden = false;
    }

    renderVariants([route, ...(route.variants || [])], show);
    show(route);
  } catch (error) {
    routeStatus.style.color = '#b91c1c';
    routeStatus.textContent = `Planovani selhalo: ${error.message}`;
  } finally {
    planButton.disabled = false;
    planButton.textContent = 'Naplanovat trasu';
  }
}

planButton.addEventListener('click', planRoute);

const MODE_LABELS = { metro: 'metro', tram: 'tram', train: 'vlak', bus: 'bus', other: 'MHD' };

function stopMarker(lat, lon, label, fillColor, radius) {
  return L.circleMarker([lat, lon], {
    pane: 'routePane', radius: radius || 5, color: '#111827',
    weight: 1.5, fillColor: fillColor || '#ffffff', fillOpacity: 1
  }).bindTooltip(label);
}

function connector(a, b) {
  // tenka spojka mezi segmenty (prestup, dochod k zastavce), jen kdyz je mezera
  const from = L.latLng(a);
  const to = L.latLng(b);
  if (from.distanceTo(to) < 15) return null;
  return L.polyline([a, b], {
    pane: 'routePane', color: '#53606f', weight: 1.5, dashArray: '1 5', opacity: 0.8
  });
}

function drawExpedition(expedition) {
  routeLayerGroup.clearLayers();
  probeMarker.remove();
  const bounds = [];
  let previousEnd = null;

  function join(point) {
    if (previousEnd) {
      const link = connector(previousEnd, point);
      if (link) routeLayerGroup.addLayer(link);
    }
  }

  const run = expedition.route.coordinates;
  for (const segment of expedition.segments) {
    if (segment.type === 'walk' && segment.coordinates && segment.coordinates.length > 1) {
      join(segment.coordinates[0]);
      routeLayerGroup.addLayer(L.polyline(segment.coordinates, {
        pane: 'routePane', color: '#53606f', weight: 2.5, dashArray: '2 6', opacity: 0.9
      }));
      bounds.push(...segment.coordinates);
      previousEnd = segment.coordinates[segment.coordinates.length - 1];
    } else if (segment.type === 'transit') {
      for (const leg of segment.legs) {
        const line = leg.coords || [[leg.from_lat, leg.from_lon], [leg.to_lat, leg.to_lon]];
        join(line[0]);
        routeLayerGroup.addLayer(L.polyline(line, {
          pane: 'routePane', color: '#53606f', weight: 3, dashArray: '8 8', opacity: 0.85
        }));
        routeLayerGroup.addLayer(stopMarker(leg.from_lat, leg.from_lon, `${leg.from} (${leg.line})`));
        routeLayerGroup.addLayer(stopMarker(leg.to_lat, leg.to_lon, `${leg.to} (${leg.line})`));
        bounds.push(...line);
        previousEnd = line[line.length - 1];
      }
    } else if (segment.type === 'run') {
      join(run[0]);
      routeLayerGroup.addLayer(L.polyline(run, {
        pane: 'routePane', color: '#ffffff', weight: 7, opacity: 0.9
      }));
      const runLine = L.polyline(run, {
        pane: 'routePane', color: '#111827', weight: 3.5, opacity: 0.95
      });
      attachDistanceProbe(runLine, run);
      routeLayerGroup.addLayer(runLine);
      if (expedition.route.is_loop) {
        routeLayerGroup.addLayer(stopMarker(run[0][0], run[0][1], 'Start i cil behu', '#008300', 7));
      } else {
        routeLayerGroup.addLayer(stopMarker(run[0][0], run[0][1], 'Start behu', '#008300', 7));
        routeLayerGroup.addLayer(stopMarker(run[run.length - 1][0], run[run.length - 1][1], 'Konec behu', '#e34948', 7));
      }
      bounds.push(...run);
      previousEnd = run[run.length - 1];
    }
  }

  if (!expedition.segments.some(segment => segment.type === 'run')) {
    routeLayerGroup.addLayer(L.polyline(run, {
      pane: 'routePane', color: '#111827', weight: 3.5, opacity: 0.95
    }));
    bounds.push(...run);
  }
  map.fitBounds(L.latLngBounds(bounds), { padding: [40, 40] });
}

function renderSegments(expedition) {
  // podrobny itinerar: kazda jizda vlastni radek, prestupy explicitne
  const lines = [];
  for (const segment of expedition.segments) {
    if (segment.type === 'walk') {
      lines.push(`${segment.desc}: ${segment.km} km (${segment.min} min)`);
    } else if (segment.type === 'transit') {
      segment.legs.forEach((leg, index) => {
        if (index > 0) {
          const previous = segment.legs[index - 1];
          const where = previous.to === leg.from ? leg.from : `${previous.to} -> ${leg.from}`;
          lines.push(`Prestup: ${where}`);
        }
        const wait = leg.wait_min ? `, cekani ~${leg.wait_min} min` : '';
        lines.push(`${MODE_LABELS[leg.mode] || leg.mode} ${leg.line}: ${leg.from} -> ${leg.to} (${leg.stops} zast., ${leg.minutes} min${wait})`);
      });
    } else {
      const from = expedition.alight ? ` z ${expedition.alight.name}` : '';
      const to = expedition.return_stop ? ` do ${expedition.return_stop.name}` : '';
      lines.push(`Beh${from}${to}: ${segment.km} km (${segment.min} min)`);
    }
  }

  // Zadne "dalsi smery" - odmitnuti kandidati se nabizeli jen jako text s odhadem
  // prinosu, zatimco prepinac variant nad panelem nabizi tytez smery hotove
  // a klikatelne (viz renderVariants).
  routeSegments.innerHTML = `<strong>Vyprava ${expedition.total_min} min / rozpocet ${expedition.budget_min} min`
    + `${expedition.within_budget ? '' : ' (PRES ROZPOCET!)'} - beh celkem ${expedition.run_km} km</strong><br>`
    + lines.map((line, index) => `${index + 1}. ${line}`).join('<br>');
  routeSegments.hidden = false;
}

async function planExpedition() {
  const position = startMarker.getLatLng();
  planExpeditionButton.disabled = true;
  planExpeditionButton.textContent = 'Planuji vypravu...';
  routeStatus.style.color = '#53606f';
  routeStatus.textContent = 'Hledam spojeni a porovnavam vypravy... v nove oblasti to muze trvat i minuty.';
  gpxButton.hidden = true;
  routeBenefit.hidden = true;
  routeVariants.hidden = true;
  routeSegments.hidden = true;
  routeDirections.hidden = true;

  try {
    const response = await fetch('/api/expedition', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat: position.lat,
        lon: position.lng,
        distance_km: Number(routeDistance.value),
        tolerance_km: Number(routeTolerance.value),
        budget_min: Number(routeBudget.value),
        pace_min_per_km: Number(routePace.value),
        weekend: routeWeekend.checked,
        quiet_weight: Number(routeQuiet.value)
      })
    });
    const expedition = await response.json();
    if (!response.ok) throw new Error(expedition.detail || response.statusText);

    function show(chosen) {
      lastRoute = chosen.route;
      drawExpedition(chosen);
      renderSegments(chosen);
      let kind = 'bez MHD (okruh ze startu je nejvyhodnejsi)';
      if (chosen.kind === 'transit') {
        const back = chosen.return_stop && chosen.return_stop.name !== chosen.alight.name
          ? `, navrat z ${chosen.return_stop.name}`
          : (chosen.return_stop ? '' : ', zpatky se bezi domu');
        kind = `pres ${chosen.alight.name}${back}`;
      }
      routeStatus.textContent = `Vyprava ${kind}.`;
      renderBenefit(chosen.route);
      renderDirections(chosen.route);
      gpxButton.hidden = false;
    }

    renderVariants([expedition, ...(expedition.variants || [])], show);
    show(expedition);
  } catch (error) {
    routeStatus.style.color = '#b91c1c';
    routeStatus.textContent = `Planovani vypravy selhalo: ${error.message}`;
  } finally {
    planExpeditionButton.disabled = false;
    planExpeditionButton.textContent = 'Naplanovat vypravu (s MHD)';
  }
}

planExpeditionButton.addEventListener('click', planExpedition);

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
