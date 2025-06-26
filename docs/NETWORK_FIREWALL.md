# Firewall y Puertos Internos

Este documento describe las reglas de red aplicadas para los servicios del MCP.

## Servicio `llm_docs-mcp`

Para reducir la exposición externa del servicio de documentos, el contenedor ya no publica el puerto `8000` al host. En lugar de ello, solo se expone internamente dentro de la red `munbot-net` mediante `expose: "8000"` en `docker-compose.yml`.

Se recomienda actualizar el firewall del host para bloquear cualquier conexión externa al puerto 8000. Los servicios que necesiten comunicarse con `llm_docs-mcp` deben hacerlo a través de la red interna de Docker.
