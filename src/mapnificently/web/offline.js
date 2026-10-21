"use strict";

// In-browser engine for the static (offline) export: answers the same /api/... requests as the
// Python server, from files written by `mapnificently export-static`. The routing is a direct
// port of mapnificently/routing/csa.py and journey.py; keep them in step.

(function () {
  const EARTH_RADIUS_M = 6371008.8;
  const UNREACHED = 1 << 30;
  const NONE = -1;
  const BOARD = 1;
  const ALIGHT = 2;
  const MIN_WALK_LEG_M = 10;
  const MAX_TIMETABLES = 3;
  const TYPES = {
    int8: Int8Array, uint8: Uint8Array, int16: Int16Array, uint16: Uint16Array,
    int32: Int32Array, uint32: Uint32Array, float32: Float32Array, float64: Float64Array,
  };

  let manifestPromise = null;
  const networks = new Map();
  const timetables = new Map(); // "city/date" -> Promise, least recently used first

  class HttpError extends Error {
    constructor(status, detail) { super(detail); this.status = status; }
  }

  async function fetchBuffer(url) {
    const response = await fetch(url);
    if (!response.ok) throw new HttpError(response.status, `cannot load ${url}`);
    return response.arrayBuffer();
  }

  function parseContainer(buffer) {
    const headerLength = new DataView(buffer).getUint32(0, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength)));
    const arrays = {};
    for (const [name, a] of Object.entries(header.arrays)) {
      arrays[name] = new TYPES[a.dtype](buffer, a.offset, a.length);
    }
    return { meta: header.meta, arrays };
  }

  function manifest() {
    if (!manifestPromise) {
      manifestPromise = fetch("data/manifest.json").then((r) => {
        if (!r.ok) throw new HttpError(r.status, "cannot load data/manifest.json");
        return r.json();
      });
      manifestPromise.catch(() => { manifestPromise = null; });
    }
    return manifestPromise;
  }

  async function city(id) {
    const m = await manifest();
    const found = m.cities.find((c) => c.id === id);
    if (!found) throw new HttpError(404, `unknown city '${id}'`);
    return found;
  }

  function network(id) {
    if (!networks.has(id)) {
      const promise = (async () => {
        const info = await city(id);
        const { meta, arrays } = parseContainer(await fetchBuffer(info.files.network));
        const [lat0, lon0] = meta.center;
        const kx = EARTH_RADIUS_M * Math.cos(lat0 * Math.PI / 180) * Math.PI / 180;
        const ky = EARTH_RADIUS_M * Math.PI / 180;
        const n = arrays.lat.length;
        const x = new Float64Array(n);
        const y = new Float64Array(n);
        for (let i = 0; i < n; i++) {
          x[i] = (arrays.lon[i] - lon0) * kx;
          y[i] = (arrays.lat[i] - lat0) * ky;
        }
        const project = (lat, lon) => [(lon - lon0) * kx, (lat - lat0) * ky];
        return { ...arrays, ...meta, n, x, y, project };
      })();
      promise.catch(() => networks.delete(id));
      networks.set(id, promise);
    }
    return networks.get(id);
  }

  function timetable(id, day) {
    const key = `${id}/${day}`;
    if (timetables.has(key)) {
      const promise = timetables.get(key);
      timetables.delete(key);
      timetables.set(key, promise);
      return promise;
    }
    const promise = (async () => {
      const info = await city(id);
      const file = info.files.timetables[day];
      if (!file) {
        throw new HttpError(409, `No offline timetable for ${day}. This export covers ${info.dates[0]} to ${info.dates[info.dates.length - 1]}.`);
      }
      const { meta, arrays } = parseContainer(await fetchBuffer(file));
      return { ...arrays, numTrips: meta.num_trips };
    })();
    promise.catch(() => timetables.delete(key));
    timetables.set(key, promise);
    while (timetables.size > MAX_TIMETABLES) timetables.delete(timetables.keys().next().value);
    return promise;
  }

  // ------------------------------------------------------------ routing (port of csa.py)

  function lowerBound(a, value) {
    let lo = 0, hi = a.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid] < value) lo = mid + 1; else hi = mid; }
    return lo;
  }

  function upperBound(a, value) {
    let lo = 0, hi = a.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid] <= value) lo = mid + 1; else hi = mid; }
    return lo;
  }

  function stopsNear(net, lat, lon, radius) {
    const [px, py] = net.project(lat, lon);
    const found = [];
    for (let i = 0; i < net.n; i++) {
      const dx = net.x[i] - px;
      const dy = net.y[i] - py;
      if (Math.abs(dx) > radius || Math.abs(dy) > radius) continue;
      const d = Math.hypot(dx, dy);
      if (d <= radius) found.push([i, d]);
    }
    return found;
  }

  function accessStops(net, lat, lon, tStart, speed, maxWalk, detour) {
    const sources = new Map();
    for (const [stop, d] of stopsNear(net, lat, lon, maxWalk / detour)) {
      sources.set(stop, tStart + Math.trunc(d * detour / speed));
    }
    return sources;
  }

  function scan(net, tt, sources, tStart, tEnd, speed, minChange, detour) {
    const n = net.n;
    const arrival = new Int32Array(n).fill(UNREACHED);
    const arrConn = new Int32Array(n).fill(NONE);
    const arrWalkFrom = new Int32Array(n).fill(NONE);
    for (const [stop, t] of sources) arrival[stop] = Math.min(arrival[stop], t);
    const ready = Int32Array.from(arrival);
    const readyConn = new Int32Array(n).fill(NONE);
    const readyWalkFrom = new Int32Array(n).fill(NONE);
    const boardConn = new Int32Array(tt.numTrips).fill(NONE);
    const secondsPerM = detour / speed;
    const { fp_ptr: ptr, fp_to: to, fp_dist: dist } = net;

    const lo = lowerBound(tt.dep_s, tStart);
    const hi = upperBound(tt.dep_s, tEnd);
    for (let i = lo; i < hi; i++) {
      const trip = tt.trip[i];
      const flags = tt.flags[i];
      if (boardConn[trip] === NONE) {
        if (!(flags & BOARD) || ready[tt.dep_stop[i]] > tt.dep_s[i]) continue;
        boardConn[trip] = i;
      }
      if (!(flags & ALIGHT)) continue;
      const t = tt.arr_s[i];
      const stop = tt.arr_stop[i];
      if (t >= arrival[stop]) continue;
      arrival[stop] = t;
      arrConn[stop] = i;
      arrWalkFrom[stop] = NONE;
      if (t + minChange < ready[stop]) {
        ready[stop] = t + minChange;
        readyConn[stop] = i;
        readyWalkFrom[stop] = NONE;
      }
      for (let k = ptr[stop]; k < ptr[stop + 1]; k++) {
        const other = to[k];
        const walk = Math.trunc(dist[k] * secondsPerM);
        const tWalk = t + walk;
        if (tWalk < arrival[other]) {
          arrival[other] = tWalk;
          arrConn[other] = i;
          arrWalkFrom[other] = stop;
        }
        const tReady = t + (walk > minChange ? walk : minChange);
        if (tReady < ready[other]) {
          ready[other] = tReady;
          readyConn[other] = i;
          readyWalkFrom[other] = stop;
        }
      }
    }
    return { tStart, tEnd, sources, arrival, arrConn, arrWalkFrom, ready, readyConn, readyWalkFrom, boardConn };
  }

  // ------------------------------------------------------------ journeys (port of journey.py)

  function routeMode(type) {
    const basic = { 0: "tram", 1: "metro", 2: "rail", 3: "bus", 4: "ferry", 5: "tram", 6: "cable car", 7: "funicular", 11: "bus", 12: "rail" };
    if (type in basic) return basic[type];
    const extended = [[100, 200, "rail"], [200, 300, "coach"], [400, 500, "metro"], [700, 900, "bus"],
      [900, 1000, "tram"], [1000, 1300, "ferry"], [1300, 1400, "cable car"], [1400, 1500, "funicular"]];
    for (const [low, high, mode] of extended) if (low <= type && type < high) return mode;
    return "other";
  }

  function stopDistance(net, a, b) { return Math.hypot(net.x[a] - net.x[b], net.y[a] - net.y[b]); }

  function distanceTo(net, stop, point) {
    const [px, py] = net.project(point.lat, point.lon);
    return Math.hypot(net.x[stop] - px, net.y[stop] - py);
  }

  function rideStops(tt, trip, first, last) {
    const stops = [];
    for (let i = first; i <= last; i++) if (tt.trip[i] === trip) stops.push(tt.dep_stop[i]);
    stops.push(tt.arr_stop[last]);
    return stops;
  }

  function legsToStop(result, net, tt, origin, stop, speed, detour) {
    const secondsPerM = detour / speed;
    const legs = [];
    let useReady = false;
    for (;;) {
      const conn = (useReady ? result.readyConn : result.arrConn)[stop];
      if (conn === NONE) {
        legs.push({ kind: "walk", from: null, to: stop, depart: result.tStart, arrive: result.sources.get(stop),
          distance: distanceTo(net, stop, origin) * detour });
        break;
      }
      const walkFrom = (useReady ? result.readyWalkFrom : result.arrWalkFrom)[stop];
      if (walkFrom !== NONE) {
        const gotOff = tt.arr_s[conn];
        const d = stopDistance(net, walkFrom, stop);
        legs.push({ kind: "walk", from: walkFrom, to: stop, depart: gotOff, arrive: gotOff + Math.trunc(d * secondsPerM), distance: d * detour });
      }
      const trip = tt.trip[conn];
      const boarded = result.boardConn[trip];
      const boardStop = tt.dep_stop[boarded];
      legs.push({ kind: "ride", from: boardStop, to: tt.arr_stop[conn], depart: tt.dep_s[boarded], arrive: tt.arr_s[conn],
        trip, path: rideStops(tt, trip, boarded, conn) });
      stop = boardStop;
      useReady = true;
    }
    legs.reverse();
    return legs.filter((leg) => leg.kind === "ride" || leg.distance >= MIN_WALK_LEG_M);
  }

  function itineraryTo(result, net, tt, origin, destination, speed, maxWalk, detour) {
    const secondsPerM = detour / speed;
    let bestT = null;
    let bestStop = null;
    const [ox, oy] = net.project(origin.lat, origin.lon);
    const [dx, dy] = net.project(destination.lat, destination.lon);
    const direct = Math.hypot(dx - ox, dy - oy);
    if (direct * detour <= maxWalk) bestT = result.tStart + Math.trunc(direct * secondsPerM);
    for (const [stop, d] of stopsNear(net, destination.lat, destination.lon, maxWalk / detour)) {
      if (result.arrival[stop] >= UNREACHED) continue;
      const t = result.arrival[stop] + Math.trunc(d * secondsPerM);
      if (bestT === null || t < bestT) { bestT = t; bestStop = stop; }
    }
    if (bestT === null || bestT > result.tEnd) return { arrive: null, legs: [] };
    if (bestStop === null) {
      return { arrive: bestT, legs: [{ kind: "walk", from: null, to: null, depart: result.tStart, arrive: bestT, distance: direct * detour }] };
    }
    const legs = legsToStop(result, net, tt, origin, bestStop, speed, detour);
    const last = distanceTo(net, bestStop, destination) * detour;
    if (last >= MIN_WALK_LEG_M) {
      legs.push({ kind: "walk", from: bestStop, to: null, depart: result.arrival[bestStop], arrive: bestT, distance: last });
    }
    return { arrive: bestT, legs };
  }

  // ------------------------------------------------------------ API emulation

  function parseDeparture(value) {
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/.exec(value || "");
    if (!match) throw new HttpError(422, "departure must look like YYYY-MM-DDTHH:MM");
    return { day: match[1], seconds: Number(match[2]) * 3600 + Number(match[3]) * 60 + Number(match[4] || 0) };
  }

  function clock(day, seconds) {
    const [y, m, d] = day.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d) + seconds * 1000).toISOString().slice(0, 19);
  }

  function checkMinutes(value, max) {
    if (!Number.isInteger(value) || value < 1 || value > max) {
      throw new HttpError(422, `minutes must be between 1 and ${max}`);
    }
  }

  function options(body) {
    return {
      speed: body.walk_speed_mps ?? 1.25,
      maxWalk: body.max_walk_m ?? 1000,
      change: (body.min_change_min ?? 2) * 60,
    };
  }

  async function reach(cityId, body) {
    const started = performance.now();
    const m = await manifest();
    checkMinutes(body.max_minutes, m.max_minutes);
    const { day, seconds } = parseDeparture(body.departure);
    const [net, tt] = await Promise.all([network(cityId), timetable(cityId, day)]);
    const { speed, maxWalk, change } = options(body);
    const tEnd = seconds + body.max_minutes * 60;
    const origins = body.origins.map((origin) => {
      const sources = accessStops(net, origin.lat, origin.lon, seconds, speed, maxWalk, m.walk_detour);
      const result = scan(net, tt, sources, seconds, tEnd, speed, change, m.walk_detour);
      const reached = [];
      for (let s = 0; s < net.n; s++) if (result.arrival[s] <= tEnd) reached.push(s);
      reached.sort((a, b) => result.arrival[a] - result.arrival[b]);
      return { origin, stop_idx: reached, arrival_s: reached.map((s) => result.arrival[s] - seconds) };
    });
    return {
      city: cityId, departure: clock(day, seconds), service_date: day, max_minutes: body.max_minutes,
      walk_speed_mps: speed, max_walk_m: maxWalk, min_change_min: change / 60, walk_detour: m.walk_detour,
      origins, computation_ms: Math.round((performance.now() - started) * 10) / 10,
    };
  }

  function journeyJson(itinerary, origin, destination, day, tStart, net) {
    const depart = clock(day, tStart);
    if (itinerary.arrive === null) return { origin, reachable: false, depart, arrive: null, duration_s: null, num_changes: 0, legs: [] };
    const point = (stop, end) => (stop === null ? end : { lat: net.lat[stop], lon: net.lon[stop] });
    const name = (stop, end) => (stop === null ? end : net.names[stop]);
    let previous = tStart;
    const legs = itinerary.legs.map((leg) => {
      const json = {
        mode: "walk", line: null, headsign: null,
        from_name: name(leg.from, "Start"), to_name: name(leg.to, "Destination"),
        depart: clock(day, leg.depart), arrive: clock(day, leg.arrive),
        wait_s: Math.max(0, leg.depart - previous), distance_m: null, num_stops: null,
        path: [point(leg.from, origin), point(leg.to, destination)],
      };
      if (leg.kind === "walk") {
        json.distance_m = Math.round(leg.distance);
      } else {
        const trip = leg.trip % net.num_trips; // timetable trips are numbered per day
        json.mode = routeMode(net.trip_route_type[trip]);
        json.line = net.lines[net.trip_line[trip]] ?? null;
        const headsign = net.trip_headsign[trip];
        json.headsign = headsign >= 0 ? net.headsigns[headsign] : null;
        json.num_stops = leg.path.length - 1;
        json.path = leg.path.map((s) => point(s, origin));
      }
      previous = leg.arrive;
      return json;
    });
    const rides = itinerary.legs.filter((leg) => leg.kind === "ride").length;
    return {
      origin, reachable: true, depart, arrive: clock(day, itinerary.arrive),
      duration_s: itinerary.arrive - tStart, num_changes: Math.max(0, rides - 1), legs,
    };
  }

  async function route(cityId, body) {
    const started = performance.now();
    const m = await manifest();
    const maxMinutes = body.max_minutes ?? m.max_minutes;
    checkMinutes(maxMinutes, m.max_minutes);
    const { day, seconds } = parseDeparture(body.departure);
    const [net, tt] = await Promise.all([network(cityId), timetable(cityId, day)]);
    const { speed, maxWalk, change } = options(body);
    const tEnd = seconds + maxMinutes * 60;
    const journeys = body.origins.map((origin) => {
      const sources = accessStops(net, origin.lat, origin.lon, seconds, speed, maxWalk, m.walk_detour);
      const result = scan(net, tt, sources, seconds, tEnd, speed, change, m.walk_detour);
      const itinerary = itineraryTo(result, net, tt, origin, body.destination, speed, maxWalk, m.walk_detour);
      return journeyJson(itinerary, origin, body.destination, day, seconds, net);
    });
    const durations = journeys.filter((j) => j.reachable).map((j) => j.duration_s);
    return {
      city: cityId, departure: clock(day, seconds), destination: body.destination, min_change_min: change / 60,
      journeys,
      fastest_s: durations.length ? Math.min(...durations) : null,
      everyone_s: durations.length === journeys.length ? Math.max(...durations) : null,
      computation_ms: Math.round((performance.now() - started) * 10) / 10,
    };
  }

  // Offline place search: stop names (grouped, so a station with many platforms appears once).
  async function geocode(cityId, query, limit) {
    const net = await network(cityId);
    const q = query.trim().toLocaleLowerCase("nb");
    if (q.length < 2) throw new HttpError(422, "search needs at least 2 characters");
    const groups = new Map();
    for (let i = 0; i < net.n; i++) {
      const lower = net.names[i].toLocaleLowerCase("nb");
      const at = lower.indexOf(q);
      if (at < 0) continue;
      const g = groups.get(net.names[i]) || { name: net.names[i], lat: 0, lon: 0, count: 0, rank: at === 0 ? 0 : 1 };
      g.lat += net.lat[i]; g.lon += net.lon[i]; g.count += 1;
      groups.set(net.names[i], g);
    }
    return [...groups.values()]
      .sort((a, b) => a.rank - b.rank || a.name.length - b.name.length || a.name.localeCompare(b.name, "nb"))
      .slice(0, limit)
      .map((g) => ({ name: g.name, label: g.name, lat: g.lat / g.count, lon: g.lon / g.count, kind: "stop" }));
  }

  async function stops(cityId) {
    const net = await network(cityId);
    return { city: cityId, ids: [], names: net.names, lat: Array.from(net.lat), lon: Array.from(net.lon) };
  }

  async function handle(path, init) {
    const url = new URL(path, location.href);
    const parts = url.pathname.replace(/^.*\/api\//, "").split("/");
    const body = init && init.body ? JSON.parse(init.body) : null;
    const m = await manifest();
    if (parts[0] === "config") return { max_minutes: m.max_minutes, offline: true, generated_at: m.generated_at };
    if (parts[0] === "cities" && parts.length === 1) return m.cities;
    if (parts[0] === "cities") {
      const [, cityId, action] = parts;
      const info = await city(cityId);
      if (!action) return info;
      if (action === "stops") return stops(cityId);
      if (action === "geocode") return geocode(cityId, url.searchParams.get("q") || "", Number(url.searchParams.get("limit") || 8));
      if (action === "reach") return reach(cityId, body);
      if (action === "route") return route(cityId, body);
    }
    throw new HttpError(404, `not available offline: ${url.pathname}`);
  }

  window.mapnificentlyOffline = { handle, HttpError };
})();
