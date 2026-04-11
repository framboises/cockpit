/* =====================================================================
   COCKPIT Field — Service Worker
   Strategie :
     - app shell : cache-first (field.html, field.css, field.js, Leaflet, icones)
     - tiles (/field/resources/tiles, unpkg, arcgis) : stale-while-revalidate
       avec limite (LRU approximative par purge FIFO)
     - API (/field/*) : network-first, fallback silencieux offline
   ===================================================================== */

const SW_VERSION = "field-sw-v1";
const APP_SHELL_CACHE = "field-shell-" + SW_VERSION;
const TILE_CACHE = "field-tiles-" + SW_VERSION;
const API_CACHE = "field-api-" + SW_VERSION;

const TILE_CACHE_MAX = 600; // ~3 Mo a ~5 Ko/tuile
const APP_SHELL_URLS = [
  "/field",
  "/field/pair",
  "/static/css/field.css",
  "/static/js/field.js",
  "/static/img/field-icon.svg",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(APP_SHELL_CACHE).then(function (cache) {
      // On charge best-effort : un echec sur unpkg ne doit pas bloquer
      return Promise.all(
        APP_SHELL_URLS.map(function (u) {
          return cache.add(u).catch(function () { /* skip */ });
        })
      );
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) {
          return k.indexOf("field-") === 0 &&
                 k !== APP_SHELL_CACHE &&
                 k !== TILE_CACHE &&
                 k !== API_CACHE;
        }).map(function (k) { return caches.delete(k); })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

function isTileRequest(url) {
  return /\/field\/resources\/tiles\//.test(url)
      || /tile\.openstreetmap\.org/.test(url)
      || /arcgisonline\.com\/.+\/World_Imagery/.test(url);
}

function isApiRequest(url) {
  return /\/field\/(inbox|me|my-fiches|position|resources\/(grid-ref|3p|gm-))/.test(url);
}

function isShellRequest(url, request) {
  if (request.mode === "navigate") return true;
  return APP_SHELL_URLS.some(function (u) { return url.indexOf(u) !== -1; });
}

async function trimCache(cacheName, max) {
  try {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length <= max) return;
    // FIFO : supprime les premieres entrees
    const toDelete = keys.length - max;
    for (let i = 0; i < toDelete; i++) {
      await cache.delete(keys[i]);
    }
  } catch (e) { /* ignore */ }
}

self.addEventListener("fetch", function (event) {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = req.url;

  // Ignore les cross-origin autres que ceux qu'on gere
  try {
    const u = new URL(url);
    const sameOrigin = u.origin === self.location.origin;
    if (!sameOrigin && !/unpkg\.com|openstreetmap\.org|arcgisonline\.com/.test(u.hostname)) {
      return;
    }
  } catch (e) { return; }

  // Tiles : stale-while-revalidate
  if (isTileRequest(url)) {
    event.respondWith(
      caches.open(TILE_CACHE).then(function (cache) {
        return cache.match(req).then(function (cached) {
          const network = fetch(req).then(function (resp) {
            if (resp && resp.status === 200) {
              cache.put(req, resp.clone()).then(function () {
                trimCache(TILE_CACHE, TILE_CACHE_MAX);
              });
            }
            return resp;
          }).catch(function () { return cached; });
          return cached || network;
        });
      })
    );
    return;
  }

  // API : network-first, fallback cache
  if (isApiRequest(url)) {
    event.respondWith(
      fetch(req).then(function (resp) {
        if (resp && resp.status === 200) {
          const clone = resp.clone();
          caches.open(API_CACHE).then(function (cache) { cache.put(req, clone); });
        }
        return resp;
      }).catch(function () {
        return caches.match(req);
      })
    );
    return;
  }

  // App shell : cache-first
  if (isShellRequest(url, req)) {
    event.respondWith(
      caches.match(req).then(function (cached) {
        return cached || fetch(req).then(function (resp) {
          if (resp && resp.status === 200) {
            const clone = resp.clone();
            caches.open(APP_SHELL_CACHE).then(function (cache) { cache.put(req, clone); });
          }
          return resp;
        }).catch(function () {
          // Pour une navigation offline, renvoyer la home
          if (req.mode === "navigate") return caches.match("/field");
        });
      })
    );
    return;
  }
});

// Message channel : permet a l'app de demander un flush (rien pour l'instant)
self.addEventListener("message", function (event) {
  if (!event.data) return;
  if (event.data.type === "SKIP_WAITING") self.skipWaiting();
});
