"use strict";

// Service worker of the static (offline) export. `mapnificently export-static` copies it to the
// site root and fills in the version, so each export starts with fresh caches.
//
// - The page, scripts and manifest are stored on install.
// - City data (stops, timetables) is stored the first time it is used.
// - Map tiles are stored as they are viewed (never bulk-downloaded: the OpenStreetMap tile
//   policy forbids that), so areas you have looked at keep their background offline.

const VERSION = "__VERSION__";
const APP_CACHE = `mapnificently-app-${VERSION}`;
const TILE_CACHE = "mapnificently-tiles";
const MAX_TILES = 3000;
const SHELL = [
  "./",
  "index.html",
  "static/app.js",
  "static/offline.js",
  "static/style.css",
  "static/vendor/leaflet/leaflet.min.js",
  "static/vendor/leaflet/leaflet.min.css",
  "data/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(APP_CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name !== APP_CACHE && name !== TILE_CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

async function trimTiles(cache) {
  const keys = await cache.keys();
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_TILES))) await cache.delete(key);
}

async function cacheFirst(request, cacheName, { trim = false } = {}) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request, { ignoreSearch: false });
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) {
    await cache.put(request, response.clone());
    if (trim) trimTiles(cache);
  }
  return response;
}

// The manifest says which dates exist; prefer a fresh copy when online.
async function networkFirst(request) {
  const cache = await caches.open(APP_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (err) {
    const hit = await cache.match(request);
    if (hit) return hit;
    throw err;
  }
}

// The page sends what it loaded before this worker took control (on the first visit).
self.addEventListener("message", (event) => {
  const { type, urls } = event.data || {};
  if (type !== "cache" || !Array.isArray(urls)) return;
  const own = urls.filter((u) => new URL(u).origin === self.location.origin);
  event.waitUntil(caches.open(APP_CACHE).then((cache) => cache.addAll(own)).catch(() => {}));
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.hostname.endsWith("tile.openstreetmap.org")) {
    event.respondWith(cacheFirst(request, TILE_CACHE, { trim: true }));
  } else if (url.origin === self.location.origin) {
    const isManifest = url.pathname.endsWith("/data/manifest.json");
    event.respondWith(isManifest ? networkFirst(request) : cacheFirst(request, APP_CACHE));
  }
});
