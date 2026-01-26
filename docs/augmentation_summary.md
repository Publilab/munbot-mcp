# Data Augmentation Summary - Transit Chatbot Dataset

**Fecha**: 2026-01-22  
**Objetivo**: Expandir el dataset de 400 a ~1,300 ejemplos con patrones chilenos

## Resultados

### Estadísticas
- **Ejemplos originales**: 411
- **Ejemplos augmentados**: 919 nuevos
- **Total final**: 1,330 ejemplos
- **Labels únicos**: 81
- **Promedio por label**: 16.4 ejemplos (antes: 5.1)

### Técnicas de Augmentación Aplicadas

#### 1. Sinónimos Chilenos
| Original | Variaciones |
|----------|------------|
| sacar | obtener, conseguir, tramitar, gestionar, pedir |
| renovar | revalidar, actualizar, reacreditar, extender |
| licencia | carnet, permiso de conducir, pase |
| multa | parte, citación, infracción, ticket |
| agendar | reservar, solicitar, pedir, apartar |
| cuánto | qué valor, qué precio, cuánta plata |

#### 2. Partículas y Rellenos Chilenos
- `po`, `pues`, `cachai`, `oye`, `mira`
- `una consulta`, `tengo una duda`, `oiga`

#### 3. Typos SMS-Style
- `qu` → `k` (ejemplo: "kiero" por "quiero")
- `ción` → `sion`
- `ll` → `y`

#### 4. Variaciones de Forma
- Conversión pregunta ↔ afirmación
- Simplificación keyword-style
- Adición de starters ("me podrías decir", "quisiera saber")

## Ejemplos de Variaciones por Tipo

### FAQ: Multas TAG
**Original**: "¿Cómo pago multas por TAG para sacar permiso de circulación?"

**Variaciones generadas**:
- "cómo cancelar multas por TAG para obtener permiso de circulación"
- "oye ¿Cómo pago multas por TAG para sacar pc?"
- "me podrías decir cómo pago multas por tag para sacar permiso circulasion"
- "kiero saber como pagar multas tag sacar permiso"

### AGENDA: Reserva de Hora
**Original**: "agendar hora"

**Variaciones generadas**:
- "reservar cita"
- "solicitar turno"
- "pedir hora po"
- "quisiera saber agendar hora"
- "cachai como agendar hora"

## Distribución por Label (Top 10)

| Label | Original | Final | Diferencia |
|-------|----------|-------|-----------|
| permcirc-pago-online | 5 | 26 | +21 |
| liccond-obtencion_profesional | 9 | 24 | +15 |
| liccond-duplicado_profesional_A2 | 7 | 24 | +17 |
| liccond-obtencion_profesional_A2 | 7 | 23 | +16 |
| permcirc-tasacion_fiscal_sii | 4 | 22 | +18 |
| liccond-duplicado_profesional_A5 | 7 | 22 | +15 |
| liccond-obtencion_profesional_A4 | 6 | 21 | +15 |
| liccond-renovacion_profesional_A1 | 7 | 21 | +14 |
| liccond-renovacion_profesional_A4 | 7 | 21 | +14 |
| liccond-duplicado_profesional_A4 | 7 | 21 | +14 |

## Próximos Pasos

1. ✅ **Validación completada**: JSON válido, IDs únicos
2. ✅ **Dataset augmentado**: 1,330 ejemplos
3. 🔄 **Siguiente**: Implementar Router Determinista
   - Cargar dataset augmentado
   - Matching fuzzy con RapidFuzz
   - Threshold tuning para FAQ vs Agenda
