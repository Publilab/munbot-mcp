# Reporte de Calibración Interpretativas

Departamento: **transito**

## Resumen
- Total queries: 62
- Accuracy (top-1): 100.00%
- Score p25 (correctos): 1.000
- Score p50 (correctos): 1.000
- Score p75 (correctos): 1.000

## Recomendación de umbrales
- `INTERP_QA_THRESHOLD`: **1.0**
- `INTERP_QA_DISAMBIGUATE`: **0.7**

## Muestras con baja confianza (top 15)
- `cobertura soap` → best `soap-cobertura_indemnizaciones` (1.000)
- `beneficiarios soap` → best `soap-beneficiarios_exclusiones` (1.000)
- `vigencia SOAP` → best `soap-vigencia_para_permiso_circulacion` (1.000)
- `¿Dónde veo las multas de tránsito no pagadas?` → best `regdeu-multas_tag-consultar_multas` (1.000)
- `¿Cómo saco el certificado de multas impagas?` → best `regdeu-multas_tag-consultar_multas` (1.000)
- `¿Puedo pagar las multas TAG en cuotas?` → best `regdeu-multas_tag-convenio_pago` (1.000)
- `No puedo sacar permiso por multas de autopista` → best `regdeu-multas_tag-permiso_circulacion_relacion` (1.000)
- `¿Qué documentos necesito para regularizar multas TAG?` → best `regdeu-multas_tag-documentos_tipicos` (1.000)
- `hoja de vida del conductor y multas` → best `regdeu-hoja_de_vida_y_multas_relacion` (1.000)
- `¿la hoja de vida muestra multas del vehículo?` → best `regdeu-hoja_de_vida_y_multas_relacion` (1.000)
- `¿Qué es el Juzgado de Policía Local?` → best `ACTPOL-JPL-000` (1.000)
- `Me llegó una citación al JPL, ¿qué significa?` → best `ACTPOL-JPL-000` (1.000)
- `Me dejaron una citación en el parabrisas, ¿sirve?` → best `ACTPOL-JPL-010` (1.000)
- `No puedo ir al juzgado, ¿qué pasa?` → best `ACTPOL-JPL-030` (1.000)
- `¿Qué pasa si falto a la citación?` → best `ACTPOL-JPL-030` (1.000)
