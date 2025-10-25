#!/usr/bin/env bash
# wait-for-it.sh: espera a que un host:puerto esté disponible
# Uso: wait-for-it.sh host:port -- comando

set -e

hostport="$1"
shift

host="${hostport%%:*}"
port="${hostport##*:}"

for i in {1..60}; do
    if nc -z "$host" "$port"; then
        echo "[wait-for-it] $host:$port está disponible!"
        exec "$@"
        exit 0
    fi
    echo "[wait-for-it] Esperando $host:$port... ($i/60)"
    sleep 1
done

echo "[wait-for-it] Timeout esperando $host:$port" >&2
exit 1
