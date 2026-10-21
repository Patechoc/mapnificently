"use strict";

// Mapnificently web map: click start points, drag the slider, see where you can get.
// The server returns the earliest arrival at every reached stop; the reachable area is drawn
// here as walking circles around those stops, so moving the slider needs no new request.

const FALLBACK_MAX_MINUTES = 180; // used if the server has no /api/config
const MAX_ORIGINS = 10;
const MAX_STOP_MARKERS = 4000;
const COLORS = ["#1f6feb", "#d6336c", "#2f9e44", "#f08c00", "#7048e8",
                "#0c8599", "#e8590c", "#5c940d", "#ae3ec9", "#495057"];

const $ = (id) => document.getElementById(id);

const state = {
  cities: [],
  city: null,
  stops: null,        // {ids, names, lat: Float64Array, lon: Float64Array}
  origins: [],        // {lat, lon, label, color, marker, reach: {idx: Int32Array, t: Int32Array}}
  detour: 1.2,
  maxMinutes: FALLBACK_MAX_MINUTES, // from /api/config
  requestSeq: 0,
};

// ---------------------------------------------------------------- helpers

async function api(path, options = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...options };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const response = await fetch(path, init);
  let payload = null;
  try { payload = await response.json(); } catch { /* empty body */ }
  if (!response.ok) {
    let detail = payload && payload.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return payload;
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function setStatus(text, isError = false) {
  const el = $("status");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function pad(n) { return String(n).padStart(2, "0"); }

function defaultDate() {
  // Next weekday, so the first view shows a normal commuting timetable.
  const d = new Date();
  if (d.getDay() === 6) d.setDate(d.getDate() + 2);
  if (d.getDay() === 0) d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function minutes() { return Number($("minutes").value); }
function walkSpeed() { return Number($("walk").value); }
function maxWalk() { return Number($("maxwalk").value); }
function mode() { return document.querySelector("input[name=mode]:checked").value; }

// ---------------------------------------------------------------- map

const map = L.map("map", { zoomControl: false, preferCanvas: true }).setView([59.9111, 10.7528], 12);
L.control.zoom({ position: "topright" }).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const stopRenderer = L.canvas({ padding: 0.2 });
const stopLayer = L.layerGroup().addTo(map);

function metresPerPixel() {
  const lat = map.getCenter().lat * Math.PI / 180;
  return 40075016.686 * Math.cos(lat) / (256 * Math.pow(2, map.getZoom()));
}

// Canvas overlay: a grey veil over the map with the reachable area cut out.
const ReachLayer = L.Layer.extend({
  onAdd(m) {
    this._canvas = L.DomUtil.create("canvas", "reach-layer");
    this._canvas.style.pointerEvents = "none";
    this._canvas.style.position = "absolute";
    m.getPane("overlayPane").appendChild(this._canvas);
    this._masks = [];
    this._frame = null;
    m.on("move zoomend resize viewreset", this.schedule, this);
    m.on("zoomstart", () => { this._canvas.style.visibility = "hidden"; });
    this.schedule();
  },

  onRemove(m) {
    L.DomUtil.remove(this._canvas);
    m.off("move zoomend resize viewreset", this.schedule, this);
  },

  schedule() {
    if (this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = null;
      this._draw();
    });
  },

  _mask(i, w, h) {
    let c = this._masks[i];
    if (!c) { c = document.createElement("canvas"); this._masks[i] = c; }
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return c;
  },

  _draw() {
    const m = this._map;
    const size = m.getSize();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(size.x * dpr);
    const h = Math.round(size.y * dpr);
    const canvas = this._canvas;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w; canvas.height = h;
      canvas.style.width = `${size.x}px`; canvas.style.height = `${size.y}px`;
    }
    L.DomUtil.setPosition(canvas, m.containerPointToLayerPoint([0, 0]));
    canvas.style.visibility = "visible";

    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!state.origins.length) return;

    const budget = minutes() * 60;
    const speed = walkSpeed();
    const cap = maxWalk();
    const pxPerMetre = 1 / (metresPerPixel() * state.detour);
    const masks = state.origins.map((origin, i) => {
      const mask = this._mask(i, w, h);
      const mctx = mask.getContext("2d");
      mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      mctx.fillStyle = "#000";
      mctx.beginPath();
      const p0 = m.latLngToContainerPoint([origin.lat, origin.lon]);
      const r0 = Math.min(budget * speed, cap) * pxPerMetre;
      mctx.moveTo(p0.x + r0, p0.y);
      mctx.arc(p0.x, p0.y, r0, 0, 2 * Math.PI);
      if (origin.reach && state.stops) {
        const { idx, t } = origin.reach;
        for (let k = 0; k < idx.length; k++) {
          const remaining = budget - t[k];
          if (remaining <= 0) break; // sorted by arrival
          const r = Math.min(remaining * speed, cap) * pxPerMetre;
          const s = idx[k];
          const p = m.latLngToContainerPoint([state.stops.lat[s], state.stops.lon[s]]);
          if (p.x < -r || p.y < -r || p.x > size.x + r || p.y > size.y + r) continue;
          mctx.moveTo(p.x + r, p.y);
          mctx.arc(p.x, p.y, r, 0, 2 * Math.PI);
        }
      }
      mctx.fill();
      return mask;
    });

    // Intersection: keep only pixels covered by every origin's mask.
    let cutouts = masks;
    if (mode() === "intersection" && masks.length > 1) {
      const combined = masks[0].getContext("2d");
      combined.setTransform(1, 0, 0, 1, 0, 0);
      combined.globalCompositeOperation = "destination-in";
      for (const mask of masks.slice(1)) combined.drawImage(mask, 0, 0);
      combined.globalCompositeOperation = "source-over";
      cutouts = [masks[0]];
    }

    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--veil") || "rgba(28,33,52,0.55)";
    ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = "destination-out";
    for (const mask of cutouts) ctx.drawImage(mask, 0, 0);
    ctx.globalCompositeOperation = "source-over";
  },
});

const reachLayer = new ReachLayer().addTo(map);

// ---------------------------------------------------------------- origins

function originIcon(color) {
  return L.divIcon({
    className: "",
    html: `<div class="origin-marker" style="background:${color}"></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

function nextColor() {
  const used = new Set(state.origins.map((o) => o.color));
  return COLORS.find((c) => !used.has(c)) || COLORS[state.origins.length % COLORS.length];
}

function addOrigin(lat, lon, label = null, { recompute = true } = {}) {
  if (state.origins.length >= MAX_ORIGINS) {
    setStatus(`At most ${MAX_ORIGINS} start points.`, true);
    return;
  }
  const origin = { lat, lon, label, color: nextColor(), reach: null };
  origin.marker = L.marker([lat, lon], { draggable: true, icon: originIcon(origin.color) })
    .addTo(map)
    .bindTooltip(() => origin.label || "Start point · drag to move, right-click to remove");
  origin.marker.on("dragend", () => {
    const p = origin.marker.getLatLng();
    origin.lat = p.lat; origin.lon = p.lng; origin.label = null; origin.reach = null;
    compute();
  });
  origin.marker.on("contextmenu", () => removeOrigin(origin));
  state.origins.push(origin);
  renderOrigins();
  if (recompute) compute();
}

function removeOrigin(origin) {
  map.removeLayer(origin.marker);
  state.origins = state.origins.filter((o) => o !== origin);
  renderOrigins();
  compute();
}

function clearOrigins() {
  for (const o of state.origins) map.removeLayer(o.marker);
  state.origins = [];
  renderOrigins();
}

function reachedCount(origin) {
  if (!origin.reach) return null;
  const budget = minutes() * 60;
  let n = 0;
  for (const t of origin.reach.t) { if (t > budget) break; n++; }
  return n;
}

function renderOrigins() {
  const list = $("origins");
  list.replaceChildren();
  for (const origin of state.origins) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = origin.color;
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = origin.label || `${origin.lat.toFixed(4)}, ${origin.lon.toFixed(4)}`;
    const meta = document.createElement("span");
    meta.className = "meta";
    const n = reachedCount(origin);
    meta.textContent = n === null ? "…" : `${n} stops`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Remove start point";
    remove.addEventListener("click", () => removeOrigin(origin));
    li.append(dot, label, meta, remove);
    list.append(li);
  }
  $("mode-field").hidden = state.origins.length < 2;
}

// ---------------------------------------------------------------- reached stops

function renderStops() {
  stopLayer.clearLayers();
  if (!$("show-stops").checked || !state.stops || !state.origins.length) return;
  const budget = minutes() * 60;
  const reached = state.origins.filter((o) => o.reach);
  if (!reached.length) return;
  // Per stop: earliest time anyone gets there (union) or when everyone has (intersection).
  const combine = mode() === "intersection" ? Math.max : Math.min;
  const times = new Map();
  const counts = new Map();
  for (const origin of reached) {
    const { idx, t } = origin.reach;
    for (let k = 0; k < idx.length && t[k] <= budget; k++) {
      const s = idx[k];
      times.set(s, times.has(s) ? combine(times.get(s), t[k]) : t[k]);
      counts.set(s, (counts.get(s) || 0) + 1);
    }
  }
  let shown = 0;
  for (const [s, t] of times) {
    if (mode() === "intersection" && counts.get(s) < reached.length) continue;
    if (++shown > MAX_STOP_MARKERS) break;
    L.circleMarker([state.stops.lat[s], state.stops.lon[s]], {
      renderer: stopRenderer, radius: 3, weight: 1, color: "#1d2330", fillColor: "#fff", fillOpacity: 1,
    })
      .bindTooltip(`${state.stops.names[s]} · ${Math.floor(t / 60)} min`)
      .addTo(stopLayer);
  }
}

// ---------------------------------------------------------------- drawing & state

function redraw() {
  $("minutes-label").textContent = minutes();
  reachLayer.schedule();
  renderOrigins();
  renderStops();
  writeHash();
}

async function compute() {
  writeHash();
  if (!state.city || !state.city.data_ready || !state.stops) { redraw(); return; }
  if (!state.origins.length) {
    setStatus("Click the map or search to add a start point.");
    redraw();
    return;
  }
  const seq = ++state.requestSeq;
  setStatus("Calculating…");
  try {
    const result = await api(`/api/cities/${state.city.id}/reach`, {
      method: "POST",
      body: {
        origins: state.origins.map((o) => ({ lat: o.lat, lon: o.lon })),
        departure: `${$("date").value}T${$("time").value}`,
        max_minutes: state.maxMinutes,
        walk_speed_mps: walkSpeed(),
        max_walk_m: maxWalk(),
      },
    });
    if (seq !== state.requestSeq) return; // a newer request is on its way
    result.origins.forEach((r, i) => {
      if (state.origins[i]) {
        state.origins[i].reach = { idx: Int32Array.from(r.stop_idx), t: Int32Array.from(r.arrival_s) };
      }
    });
    state.detour = result.walk_detour;
    setStatus(`Computed in ${Math.round(result.computation_ms)} ms for departure ${result.departure.slice(0, 16).replace("T", " ")}.`);
  } catch (err) {
    if (seq === state.requestSeq) setStatus(err.message, true);
  }
  redraw();
}

const computeSoon = debounce(compute, 300);

// ---------------------------------------------------------------- cities

async function selectCity(id, { fitView = true } = {}) {
  const city = state.cities.find((c) => c.id === id) || state.cities[0];
  if (!city) return;
  state.city = city;
  state.stops = null;
  $("city").value = city.id;
  $("attribution").textContent = city.attribution;
  clearOrigins();
  stopLayer.clearLayers();
  if (fitView) map.setView([city.center.lat, city.center.lon], 12);
  if (!city.data_ready) {
    setStatus(`No timetable data for ${city.name} yet. Run: mapnificently data fetch ${city.id}`, true);
    redraw();
    return;
  }
  setStatus(`Loading ${city.name} stops…`);
  try {
    const stops = await api(`/api/cities/${city.id}/stops`);
    if (state.city !== city) return;
    state.stops = { ids: stops.ids, names: stops.names, lat: Float64Array.from(stops.lat), lon: Float64Array.from(stops.lon) };
    setStatus(`${stops.ids.length} stops loaded. Click the map or search to add a start point.`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------------------------------------------------------------- search

let searchHits = [];

function hideResults() { $("search-results").hidden = true; }

function chooseHit(hit) {
  addOrigin(hit.lat, hit.lon, hit.label);
  map.panTo([hit.lat, hit.lon]);
  $("search").value = "";
  hideResults();
}

const runSearch = debounce(async () => {
  const q = $("search").value.trim();
  if (q.length < 2 || !state.city) { hideResults(); return; }
  try {
    searchHits = await api(`/api/cities/${state.city.id}/geocode?q=${encodeURIComponent(q)}`);
  } catch (err) {
    setStatus(err.message, true);
    return;
  }
  const list = $("search-results");
  list.replaceChildren();
  for (const hit of searchHits) {
    const li = document.createElement("li");
    li.textContent = hit.label;
    if (hit.kind) {
      const small = document.createElement("small");
      small.textContent = hit.kind;
      li.append(small);
    }
    li.addEventListener("mousedown", (e) => { e.preventDefault(); chooseHit(hit); });
    list.append(li);
  }
  list.hidden = searchHits.length === 0;
}, 300);

// ---------------------------------------------------------------- URL hash (shareable views)

function writeHash() {
  if (!state.city) return;
  const params = new URLSearchParams({
    city: state.city.id,
    t: minutes(),
    at: `${$("date").value}T${$("time").value}`,
    w: walkSpeed(),
    mw: maxWalk(),
    mode: mode(),
  });
  if (state.origins.length) {
    params.set("o", state.origins.map((o) => `${o.lat.toFixed(5)},${o.lon.toFixed(5)}`).join(";"));
  }
  history.replaceState(null, "", `#${params}`);
}

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const origins = (params.get("o") || "").split(";").filter(Boolean)
    .map((s) => s.split(",").map(Number)).filter((p) => p.length === 2 && p.every(Number.isFinite));
  return {
    city: params.get("city"),
    minutes: params.get("t"),
    at: params.get("at"),
    walk: params.get("w"),
    maxWalk: params.get("mw"),
    mode: params.get("mode"),
    origins,
  };
}

function setSelect(id, value) {
  if (value !== null && [...$(id).options].some((o) => o.value === value)) $(id).value = value;
}

// ---------------------------------------------------------------- wiring

map.on("click", (e) => addOrigin(e.latlng.lat, e.latlng.lng));
$("minutes").addEventListener("input", redraw);
$("walk").addEventListener("change", compute);
$("maxwalk").addEventListener("change", compute);
$("date").addEventListener("change", computeSoon);
$("time").addEventListener("change", computeSoon);
$("show-stops").addEventListener("change", renderStops);
for (const radio of document.querySelectorAll("input[name=mode]")) radio.addEventListener("change", redraw);
$("city").addEventListener("change", async () => { await selectCity($("city").value); compute(); });
$("search").addEventListener("input", runSearch);
$("search").addEventListener("blur", hideResults);
$("search").addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideResults();
  if (e.key === "Enter" && searchHits.length) chooseHit(searchHits[0]);
});
$("collapse").addEventListener("click", () => {
  const collapsed = $("panel").classList.toggle("collapsed");
  $("collapse").textContent = collapsed ? "+" : "–";
});

async function init() {
  const hash = readHash();
  try {
    state.maxMinutes = (await api("/api/config")).max_minutes;
  } catch { /* older server: keep the fallback */ }
  $("minutes").max = state.maxMinutes;
  $("date").value = defaultDate();
  $("time").value = "08:00";
  if (hash.at && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(hash.at)) {
    [$("date").value, $("time").value] = hash.at.split("T");
  }
  if (hash.minutes) $("minutes").value = hash.minutes;
  setSelect("walk", hash.walk);
  setSelect("maxwalk", hash.maxWalk);
  if (hash.mode === "intersection") document.querySelector("input[value=intersection]").checked = true;

  try {
    state.cities = await api("/api/cities");
  } catch (err) {
    setStatus(`Cannot load cities: ${err.message}`, true);
    return;
  }
  const select = $("city");
  for (const city of state.cities) {
    const option = document.createElement("option");
    option.value = city.id;
    option.textContent = city.data_ready ? city.name : `${city.name} (no data yet)`;
    select.append(option);
  }
  const preferred = hash.city || (state.cities.find((c) => c.data_ready) || state.cities[0] || {}).id;
  await selectCity(preferred, { fitView: true });
  for (const [lat, lon] of hash.origins) addOrigin(lat, lon, null, { recompute: false });
  if (hash.origins.length) map.panTo(hash.origins[0]);
  compute();
}

init();
