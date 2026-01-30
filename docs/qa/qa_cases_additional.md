# Casos QA adicionales (manuales)

## Desambiguación
- "¿Horario?" → debe preguntar si es horario de atención o agendamiento.
- "Necesito hora" → debe ir a Agenda, no FAQ.

## Fallback
- "¿Cuánto cuesta el parquímetro en calle X?" (sin fuente local) → derivar.
- "¿Puedo retirar mi licencia un tercero?" (sin fuente local) → derivar.

## Degradación
- Simular LLM off: aún debe responder FAQ determinista.
- Simular embeddings off: debe usar reglas mínimas.

## Agenda/Reclamos
- Agenda: validar fecha/horario y confirmación.
- Reclamos: validar RUT/email y cierre con comprobante.
