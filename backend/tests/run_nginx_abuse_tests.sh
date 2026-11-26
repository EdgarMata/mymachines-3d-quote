#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
fixture=$(mktemp -d)
name="mm006-nginx-${fixture##*/}"
runner="${name}-runner"
cleanup() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
        # Synthetic fixture only. Retain module rejection reasons before removal;
        # the ordinary Compose failure logger cannot see these disposable names.
        docker exec "$name" nginx -V >&2 2>&1 || true
        docker logs --tail 250 "$name" >&2 2>&1 || true
    fi
    docker rm -f "$runner" "$name" >/dev/null 2>&1 || true
    rm -rf "$fixture"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
python3 "$repo/backend/tests/nginx_abuse_integration.py" prepare "$fixture"
docker run -d --pull never --name "$name" --network none \
    -v "$fixture/default.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$fixture/trusted.conf:/etc/nginx/trusted-proxies.conf:ro" \
    -v "$repo/frontend/15-validate-trusted-proxies.sh:/docker-entrypoint.d/15-validate-trusted-proxies.sh:ro" \
    -e REQUIRE_TRUSTED_PROXY=true \
    mymachines-3d-quote-frontend:latest >/dev/null
docker exec "$name" nginx -t
# Exercise the shipped entrypoint + Alpine awk + native IPv6 parser, not only
# the guard's standard-library test harness. No daemon or external network.
for rule in 'set_real_ip_from ::1/128;' 'set_real_ip_from :::;' \
    'set_real_ip_from 0.0.0.0/0;' 'set_real_ip_from 127.0.0.2; include /tmp/injected;'; do
    printf '%s\n' "$rule" > "$fixture/startup.conf"
    status=0
    docker run --rm --pull never --name "$runner" --network none \
        -e REQUIRE_TRUSTED_PROXY=true \
        -v "$fixture/startup.conf:/etc/nginx/trusted-proxies.conf:ro" \
        mymachines-3d-quote-frontend:latest nginx -t > "$fixture/startup.log" 2>&1 || status=$?
    case "$rule:$status" in
        'set_real_ip_from ::1/128;:0') ;;
        'set_real_ip_from ::1/128;:'*) cat "$fixture/startup.log"; exit 1 ;;
        *:1) ;;
        *) cat "$fixture/startup.log"; echo "Unexpected startup-check status: $status" >&2; exit 1 ;;
    esac
done
docker run --rm --pull never --name "$runner" --network "container:$name" \
    -v "$repo/backend/tests:/app/tests:ro" \
    -v "$repo/frontend:/source/frontend:ro" \
    mymachines-3d-quote-backend:latest python /app/tests/nginx_abuse_integration.py "$@"
