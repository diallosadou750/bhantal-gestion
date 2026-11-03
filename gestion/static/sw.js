/* Service worker : met l'interface (pas les donnÃ©es) en cache pour qu'elle s'ouvre mÃªme sans connexion. */
const CACHE = 'gestion-shell-v57';
const SHELL = ['/', '/i18n.js?v=57', '/app.js?v=57', '/pieces.js', '/tableau.js', '/ventes.js?v=57', '/relances.js', '/paie.js', '/devis.js', '/clients.js', '/produits.js?v=57', '/achats.js', '/caisse.js', '/banque.js', '/rapports.js', '/compta.js', '/rappels.js', '/admin.js?v=57', '/aide.js', '/style.css?v=57', '/manifest.webmanifest', '/brand-bhantal.jpg', '/icon.svg', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;   // les donnÃ©es ne sont jamais mises en cache ici
  e.respondWith(
    fetch(e.request).then((res) => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); } return res; })
      .catch(() => caches.match(e.request).then((r) => r || caches.match('/')))
  );
});
