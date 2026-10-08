/* =====================================================================
   COCKPIT Field — Service Worker
   Strategie :
     - app shell : cache-first (field.html, field.css, field.js, Leaflet, icones)
     - tiles (/field/resources/tiles, unpkg, arcgis) : stale-while-revalidate
       avec limite (LRU approximative par purge FIFO)
     - API (/field/*) : network-first, fallback silencieux offline
     - etat temps reel (fil de messages, missions disponibles, session) :
       jamais intercepte, reseau direct
     - app shell : correspondance EXACTE du chemin (plus de sous-chaine)
   ===================================================================== */

const SW_VERSION = "field-sw-v46";
const APP_SHELL_CACHE = "field-shell-" + SW_VERSION;
const TILE_CACHE = "field-tiles-" + SW_VERSION;
const API_CACHE = "field-api-" + SW_VERSION;

const TILE_CACHE_MAX = 600; // ~3 Mo a ~5 Ko/tuile
// On NE met PAS /field/pair ni /field/denied dans le shell : ces routes
// repondent par redirect quand la tablette est paire (ou non revoquee), donc
// cache.add() echoue silencieusement et le navigateur se retrouve avec une
// requete non gerable -> ERR_FAILED. Pour les navigations, on utilise
// network-first avec fallback cache (voir handler fetch ci-dessous).
const APP_SHELL_URLS = [
  "/field",
  "/field/manuel",
  "/static/css/field.css",
  "/static/css/field_manuel.css",
  "/static/js/field.js",
  "/static/img/field-icon.svg",
  "/static/img/field-icon-192.png",
  "/static/js/field_install_hint.js",
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
  return /\/field\/(inbox|me|my-fiches|position|photos\/|pco-categories|resources\/(grid-ref|3p|gm-|map-bundle))/.test(url);
}

// Requetes a ne JAMAIS servir depuis un cache : etat temps reel (fil de
// messages, missions disponibles, verification de session). On laisse le
// navigateur faire la requete reseau normale.
function isLiveRequest(url) {
  return /\/field\/(thread\/|available-missions|denied\/check|status(\?|$)|push\/|declarations)/.test(url);
}

// Correspondance EXACTE du chemin (et de l'URL complete pour unpkg). L'ancien
// test par sous-chaine ("/field" est dans la liste) classait TOUTE requete GET
// contenant "/field" en app shell cache-first : /field/thread/<id> etait servi
// depuis le cache pour toujours, d'ou des reponses du PC Org qui n'apparaissaient
// jamais dans la conversation, meme apres l'envoi d'une reponse.
function isShellRequest(url, request) {
  // Les navigations sont traitees a part (network-first dans le handler).
  var path = url;
  try {
    var u = new URL(url);
    path = (u.origin === self.location.origin) ? u.pathname : (u.origin + u.pathname);
  } catch (e) { /* url brute */ }
  return APP_SHELL_URLS.indexOf(path) !== -1;
}

function isNavigationRequest(request) {
  return request.mode === "navigate"
      || (request.method === "GET" && (request.headers.get("accept") || "").indexOf("text/html") !== -1);
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

  // Etat temps reel : jamais de cache, reseau direct.
  if (isLiveRequest(url)) return;

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

  // Navigations (HTML) : network-first avec fallback cache.
  // C'est crucial pour /field, /field/pair, /field/denied dont la reponse
  // depend de l'etat de la session - on ne peut pas servir une reponse
  // potentiellement perimee.
  if (isNavigationRequest(req)) {
    event.respondWith(
      fetch(req).then(function (resp) {
        // Cache la reponse seulement si succes plein 200 et meme origin.
        if (resp && resp.status === 200 && resp.type === "basic") {
          const clone = resp.clone();
          caches.open(APP_SHELL_CACHE).then(function (cache) { cache.put(req, clone); });
        }
        return resp;
      }).catch(function () {
        // Hors-ligne : on tente le cache exact puis /field, puis une page
        // synthetique pour ne JAMAIS renvoyer undefined (sinon ERR_FAILED).
        return caches.match(req).then(function (m) {
          if (m) return m;
          return caches.match("/field").then(function (m2) {
            if (m2) return m2;
            return new Response(
              "<!doctype html><meta charset=\"utf-8\"><title>Hors ligne</title>"
              + "<body style=\"font-family:sans-serif;background:#0f172a;color:#e2e8f0;"
              + "display:flex;align-items:center;justify-content:center;height:100vh;"
              + "margin:0;text-align:center;padding:24px\">"
              + "<div><h1 style=\"color:#ef4444\">Hors ligne</h1>"
              + "<p>Reconnectez-vous au reseau pour continuer.</p></div></body>",
              { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
            );
          });
        });
      })
    );
    return;
  }

  // App shell statique (CSS/JS/images) : cache-first
  if (isShellRequest(url, req)) {
    event.respondWith(
      caches.match(req).then(function (cached) {
        return cached || fetch(req).then(function (resp) {
          if (resp && resp.status === 200) {
            const clone = resp.clone();
            caches.open(APP_SHELL_CACHE).then(function (cache) { cache.put(req, clone); });
          }
          return resp;
        }).catch(function () { /* ignore */ });
      })
    );
    return;
  }
});

// ---------------------------------------------------------------------------
// Web Push : reception et affichage de notification
// ---------------------------------------------------------------------------
self.addEventListener("push", function (event) {
  if (!event.data) return;
  var payload;
  try { payload = event.data.json(); } catch (e) { return; }
  var title = payload.title || "COCKPIT Field";
  var isSos = payload.type === "sos";
  var options = {
    body: payload.body || "",
    // PNG : Android et iOS n'affichent pas d'icone SVG dans une notification
    icon: "/static/img/field-icon-192.png",
    badge: "/static/img/field-icon.svg",
    tag: payload.tag || "field-push",
    renotify: true,
    silent: false,
    vibrate: isSos
      ? [500, 200, 500, 200, 500, 200, 500, 200, 500]
      : [200, 100, 200, 100, 400],
    requireInteraction: !!isSos,
    data: { url: payload.url || "/field", type: payload.type || null },
  };
  // Prevenir les pages ouvertes : elles relisent tout de suite l'inbox et
  // les fiches au lieu d'attendre le prochain cycle de polling (qui peut
  // etre ralenti ou gele par le navigateur quand l'app est en arriere-plan).
  var notifyClients = clients.matchAll({ type: "window", includeUncontrolled: true })
    .then(function (list) {
      list.forEach(function (c) {
        try { c.postMessage({ type: "field-push", pushType: payload.type || null }); } catch (e) { /* ignore */ }
      });
    }).catch(function () { /* ignore */ });
  event.waitUntil(Promise.all([
    self.registration.showNotification(title, options),
    notifyClients,
  ]));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/field";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
      // Focus un onglet existant si possible
      for (var i = 0; i < list.length; i++) {
        if (list[i].url.indexOf("/field") !== -1 && "focus" in list[i]) {
          // App deja ouverte : lui transmettre la cible (constat a ouvrir...)
          try { list[i].postMessage({ type: "field-open-url", url: url }); } catch (e) { /* ancien client */ }
          return list[i].focus();
        }
      }
      // Sinon ouvrir un nouvel onglet
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});

// Message channel : permet a l'app de demander un flush (rien pour l'instant)
self.addEventListener("message", function (event) {
  if (!event.data) return;
  if (event.data.type === "SKIP_WAITING") self.skipWaiting();
});
