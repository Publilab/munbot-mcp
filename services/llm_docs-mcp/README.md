
# `llm_docs-mcp`

Servicio que expone una API basada en FastAPI para consultar documentos.

## Variables de Entorno

- **`ALLOWED_IPS`**: lista separada por comas de IPs o rangos CIDR
  autorizados para acceder a la pasarela. El valor por defecto incluye la
  red de Docker:

  ```
  127.0.0.1,172.18.0.0/16,192.168.1.100
  ```
