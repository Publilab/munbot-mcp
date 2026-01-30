# Checklist de Release (QA → Producción)

## Antes de release
- [ ] Suite de regresión ejecutada (≥95% pass).
- [ ] Casos de desambiguación y fallback revisados.
- [ ] Agenda y Reclamos: flujo completo sin bloqueo.
- [ ] Validación de gobernanza OK (script estricto).
- [ ] Fuentes locales pendientes marcadas como `observacion`.

## Durante release
- [ ] Activar feature flags por etapas.
- [ ] Monitoreo en vivo de p95 y fallback rate.

## Post-release (24–72h)
- [ ] Revisar top gaps.
- [ ] Revisar feedback negativo.
- [ ] Registrar hallazgos en backlog.
