#!/usr/bin/env bash
# Smoke-test a built image the way Cloud Run runs it: PORT set from outside, refresh disabled.
#
#   scripts/docker_smoke.sh [IMAGE]        (default: mapnificently:local; `make docker-test`)
#
# Starts a container on a free local port, checks the page, the API, every city's data, a
# reach query, a route and that refreshing is refused, then removes the container.
set -euo pipefail

IMAGE=${1:-mapnificently:local}
NAME=mapnificently-smoke-$$
fail() { echo "FAIL: $*" >&2; docker logs "$NAME" 2>&1 | tail -20 >&2; exit 1; }

docker run -d --name "$NAME" -e PORT=9090 -p 127.0.0.1::9090 "$IMAGE" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
BASE="http://$(docker port "$NAME" 9090/tcp | head -1)"

for _ in $(seq 60); do
    curl -sf "$BASE/api/config" >/dev/null && break
    sleep 1
done
curl -sf "$BASE/api/config" >/dev/null || fail "the server did not start"
echo "server up at $BASE (PORT=9090 inside the container)"

[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")" = 200 ] || fail "the web map does not load"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/docs")" = 200 ] || fail "the API docs do not load"
echo "web map and API docs: OK"

curl -sf "$BASE/api/cities" | python3 -c '
import json, sys
cities = json.load(sys.stdin)
missing = [c["id"] for c in cities if not c["data_ready"]]
assert not missing, f"no data for {missing}"
print("data ready for:", ", ".join(c["id"] for c in cities))
' || fail "some cities have no data"

DAY=$(python3 -c 'import datetime as d; t = d.date.today() + d.timedelta(1)
while t.weekday() > 4: t += d.timedelta(1)
print(t)')
curl -sf -X POST "$BASE/api/cities/oslo/reach" -H 'Content-Type: application/json' \
    -d "{\"origins\": [{\"lat\": 59.9109, \"lon\": 10.7536}], \"departure\": \"${DAY}T08:00\", \"max_minutes\": 30}" \
    | python3 -c '
import json, sys
stops = len(json.load(sys.stdin)["origins"][0]["stop_idx"])
assert stops > 500, f"only {stops} stops reached"
print(f"reach from Oslo S in 30 min: {stops} stops")
' || fail "reach query"

curl -sf -X POST "$BASE/api/cities/moss/route" -H 'Content-Type: application/json' \
    -d "{\"origins\": [{\"lat\": 59.43214, \"lon\": 10.65705}], \"destination\": {\"lat\": 59.96678, \"lon\": 10.7355}, \"departure\": \"${DAY}T08:00\"}" \
    | python3 -c '
import json, sys
journey = json.load(sys.stdin)["journeys"][0]
assert journey["reachable"], "not reachable"
rides = " -> ".join(leg["mode"] + " " + str(leg["line"]) for leg in journey["legs"] if leg["mode"] != "walk")
print("route Moss stasjon -> Nasjonalarkivet:", journey["duration_s"] // 60, "min (" + rides + ")")
' || fail "route query"

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/cities/oslo/refresh")
[ "$code" = 403 ] || fail "refresh should be refused (403), got $code"
echo "refresh refused: OK"

echo "memory in use: $(docker stats --no-stream --format '{{.MemUsage}}' "$NAME")"
echo "image size: $(docker image ls "$IMAGE" --format '{{.Size}}')"
echo "smoke test passed"
