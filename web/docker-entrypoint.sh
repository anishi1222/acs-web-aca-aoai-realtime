#!/usr/bin/env sh
set -eu

: "${API_UPSTREAM:=http://host.docker.internal:8000}"

# Render config to a writable location for non-root.
envsubst '${API_UPSTREAM}' < /app/nginx.conf.template > /tmp/nginx.conf

exec nginx -c /tmp/nginx.conf -g 'daemon off;'
