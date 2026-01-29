# Metadata KB (estándar mínimo)

## 1) Metadata a nivel de archivo (obligatoria)
- `version`
- `updated_at` (YYYY-MM-DD)
- `status` (`draft` | `revision` | `aprobado` | `publicado`)
- `approved_by` (nombre/rol)

## 2) Metadata a nivel de entrada (obligatoria)
Se agrega dentro de cada trámite/elemento.

- `entry_status` (`draft` | `revision` | `aprobado` | `publicado` | `observacion`)
- `entry_updated_at` (YYYY-MM-DD)
- `entry_approved_by` (nombre/rol)
- `entry_source` (ley/ordenanza/documento local)
- `entry_vigencia` (fecha o “vigente”)

Si no hay fuente local: usar `entry_status=observacion` y derivar.

## 3) Ejemplo mínimo
```
{
  "version": "1.2.0",
  "updated_at": "2026-01-29",
  "status": "revision",
  "approved_by": "Owner Municipal",
  "tramites": [
    {
      "id": "liccond-obtencion_profesional",
      "entry_status": "aprobado",
      "entry_updated_at": "2026-01-29",
      "entry_approved_by": "Owner Municipal",
      "entry_source": "Ley 18.290",
      "entry_vigencia": "vigente"
    }
  ]
}
```
