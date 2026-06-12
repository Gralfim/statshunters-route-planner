const map=L.map('map').setView([50.0755,14.4378],11);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
fetch('/api/health')
 .then(r=>r.json())
 .then(console.log);
