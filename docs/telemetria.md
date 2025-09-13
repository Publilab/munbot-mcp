# Telemetría

Este documento describe cómo analizar logs JSON del agente para obtener métricas.

## Cálculo de tasa `n/a` y distribución de handovers

Suponiendo un archivo `logs.json` con eventos en formato JSON line, se pueden extraer
las métricas directamente en la línea de comandos:

```bash
# Tasa de respuestas n/a
jq -r 'select(.event=="trace.turn") | .answer' logs.json \
  | awk '/^n\/a$/{na++} {total++} END {printf "%.2f\n", na/total}'

# Distribución de handovers
jq -r 'select(.event=="handover") | .department' logs.json \
  | awk '{count[$1]++} END {for (d in count) printf "%s %d\n", d, count[d]}'
```

## Latencia p95 del agente

Los eventos `trace.end` incluyen un campo `duration_ms` con el tiempo de respuesta. El
percentil 95 se obtiene ordenando los valores y seleccionando el índice
correspondiente:

```bash
jq -r 'select(.event=="trace.end") | .duration_ms' logs.json \
  | sort -n \
  | awk '{a[NR]=$1} END {idx=int(0.95*NR); print a[idx]}'
```

## Redacción de datos sensibles y hashing de `session_id`

Los logs eliminan correos, teléfonos y RUT reemplazándolos por `<redacted>` antes de ser
almacenados. Además, el identificador de sesión se hashifica con SHA-256 y una sal,
truncándolo a 12 caracteres para evitar exponer la `session_id` original.

