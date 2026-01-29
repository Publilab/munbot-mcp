# Workflow de contenido KB (mínimo viable)

## Estados
`draft → revisión → aprobado → publicado`

## Roles
- **Owner Municipal**: aprueba contenido y define bordes.
- **Editor KB**: actualiza archivos y completa metadata.
- **QA Conversacional**: valida tono, coherencia y trazabilidad.

## Reglas
- Nada se publica sin estado **aprobado**.
- Toda entrada debe incluir `entry_source` y `entry_vigencia`.
- Cambios se registran en `docs/kb_changelog.md`.

## Publicación (gate)
1) Validar esquema + metadata (script de gobernanza).
2) Confirmar estado **aprobado**.
3) Crear snapshot (tag git o copia en `releases/`).
