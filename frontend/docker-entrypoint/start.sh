#!/bin/sh
set -eu
umask 077

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

mode=${DASHBOARD_DATA_MODE:-demo}
backend_url=${DASHBOARD_BACKEND_URL:-}
upstream=${DASHBOARD_API_UPSTREAM:-}
resolver=${DASHBOARD_DNS_RESOLVER:-127.0.0.11}
archive_date=${DASHBOARD_DEFAULT_DATE:-}
if [ -n "$archive_date" ]; then
    jq -en --arg value "$archive_date" '$value | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")' >/dev/null || fail "Invalid archive date"
fi

case "$mode" in
    demo|api) ;;
    *) fail 'DASHBOARD_DATA_MODE must be demo or api.' ;;
esac

# This value becomes a visible browser link. Never allow credentials in its authority.
if [ -n "$backend_url" ]; then
    jq -en --arg url "$backend_url" \
      '$url | test("\\Ahttps?://[A-Za-z0-9.-]+(:[0-9]{1,5})?([/?#][^[:space:][:cntrl:]\\\\]*)?\\z")' \
      > /dev/null || fail 'DASHBOARD_BACKEND_URL must be an absolute HTTP(S) URL without credentials or whitespace.'
fi

if [ "$mode" = api ]; then
    # Only an HTTP(S) origin is accepted: no paths, user info, or nginx syntax.
    jq -en --arg url "$upstream" \
      '$url | test("\\Ahttps?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?/?\\z")' \
      > /dev/null || fail 'API mode requires DASHBOARD_API_UPSTREAM as an HTTP(S) origin, for example http://api:8000.'
    jq -en --arg resolver "$resolver" \
      '$resolver | test("\\A[0-9]{1,3}(\\.[0-9]{1,3}){3}(:[0-9]{1,5})?\\z")' \
      > /dev/null || fail 'DASHBOARD_DNS_RESOLVER must be an IPv4 address, optionally followed by a port.'
    upstream=${upstream%/}
    cat > /tmp/dashboard-api.conf <<EOF
        location /api/ {
            resolver ${resolver} ipv6=off valid=30s;
            resolver_timeout 5s;
            set \$dashboard_upstream "${upstream}";
            proxy_pass \$dashboard_upstream\$request_uri;
            proxy_http_version 1.1;
            proxy_set_header Host \$proxy_host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_set_header Connection "";
            proxy_connect_timeout 5s;
            proxy_read_timeout 30s;
            proxy_ssl_server_name on;
            proxy_ssl_verify on;
            proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
            add_header Cache-Control "no-store" always;
        }
EOF
else
    cat > /tmp/dashboard-api.conf <<'EOF'
        location /api/ {
            default_type application/json;
            return 503 '{"error":"Dashboard API is not configured; demo mode is enabled."}';
        }
EOF
fi

# jq escapes JSON strings; environment values are never evaluated as shell code.
jq -n --arg mode "$mode" --arg backend "$backend_url" --arg date "$archive_date" \
  '{dataMode: $mode, apiUrl: "/api/dashboard", backendUrl: $backend} + (if $date == "" then {} else {defaultDate: $date} end)' \
  > /tmp/runtime-config.json

exec "$@"
