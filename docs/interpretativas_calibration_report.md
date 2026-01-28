# Reporte de Calibración Interpretativas

Departamento: **transito**

## Resumen
- Total queries: 23
- Score p25: 0.454
- Score p50: 0.549
- Score p75: 0.616
- Score min: 0.384
- Score max: 0.721

## Recomendación de umbrales
- `INTERP_QA_THRESHOLD`: **0.549**
- `INTERP_QA_DISAMBIGUATE`: **0.55**

## Muestras con baja confianza (top 15)
- `¿Se puede rendir el examen práctico en un vehículo con transmisión automática?` → best `regdeu-multas_tag-monto_beneficio` (0.384)
- `¿Qué debo hacer si mi vehículo no va a circular durante todo el año?` → best `ACTPOL-JPL-080` (0.387)
- `¿Cómo puedo solicitar la instalación de “lomos de toro” o reductores de velocidad en mi barrio?` → best `ACTPOL-JPL-100` (0.418)
- `¿Cómo obtengo un estacionamiento reservado para personas con discapacidad frente a mi domicilio?` → best `ACTPOL-JPL-020` (0.423)
- `¿Puedo conducir en el extranjero con mi licencia chilena, especialmente si está prorrogada?` → best `ACTPOL-JPL-040` (0.439)
- `Me retuvieron la licencia por una infracción, ¿puede otra persona ir a retirarla por mí después de pagar?` → best `ACTPOL-JPL-060` (0.454)
- `¿por qué me rechazaron la revisión técnica?` → best `ACTPOL-JPL-070` (0.461)
- `¿Debo llevar la boleta de citación al presentarme al Juzgado?` → best `ACTPOL-JPL-020` (0.468)
- `¿Cómo acredito el cambio de propietario del vehículo al renovar el permiso?` → best `regdeu-multas_tag-consultar_multas` (0.503)
- `¿Cómo puedo cambiar los datos del vehículo en el permiso de circulación (por ejemplo, color, modelo, etc.)?` → best `regdeu-multas_tag` (0.520)
- `¿Qué hago si vendí mi vehículo y aún aparece a mi nombre en el permiso de circulación?` → best `regdeu-certificado_multas_empadronadas` (0.547)
- `¿Dónde y cómo puedo pagar una multa de tránsito?` → best `regdeu-multas_tag-pago_contado` (0.549)
- `¿qué significa esta observación en mi licencia?` → best `soap-definicion_obligatoriedad` (0.552)
- `¿Puedo renovar mi licencia de conducir en una comuna distinta a la que la emitió originalmente?` → best `ACTPOL-JPL-100` (0.552)
- `¿Qué pasa si no pago una multa de tránsito?` → best `regdeu-multas_tag-incumplimiento_convenio` (0.567)
