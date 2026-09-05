// Quartz Wallet Service Worker — offline-capable PWA
// CACHE_NAME MUST be bumped on every deploy (v1 froze stale shells on
// clients — bumped to v3 2026-08-22 after the Jan-1970 tx date bug;
// v5 2026-08-30 payment streams + lanes release;
// v6 2026-08-30 unlock fix — import keeps its PIN-lock when saving;
// v7 2026-08-30 tap-to-unlock badge + registration pinned sw.js?v=7 —
// Cloudflare was edge-caching sw.js for 4h and holding old workers hostage;
// v8 2026-08-30 sign-message fix — loadCrypto() at boot so nacl is ready on
// the reload+unlock path too).
// Shell strategy is now stale-while-revalidate: serve cache instantly,
// fetch the fresh copy in the background, and swap it in for next load.
const CACHE_NAME = 'quartz-wallet-v10';
const ASSETS = [
    '/wallet/',
    '/wallet/manifest.json',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    // Network-first for API calls, cache-first for app shell
    if (event.request.url.includes('/api/')) {
        event.respondWith(fetch(event.request));
    } else {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                const fresh = fetch(event.request).then((response) => {
                    if (response && response.status === 200) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    }
                    return response;
                }).catch(() => cached);
                return cached || fresh;
            })
        );
    }
});
