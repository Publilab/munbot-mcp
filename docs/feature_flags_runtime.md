# Feature Flags (runtime) — mínimos

## Flags sugeridas
- `FAQ_ONLY_MODE` (solo determinista)
- `RERANK_ENABLED`
- `EMBEDDINGS_ENABLED`
- `INTERP_LLM_MODE` (off/gemini/llama_cpp)
- `LLM_TRIAGE_ENABLED`

## Degradación
1) Si falla rerank → desactivar `RERANK_ENABLED`
2) Si falla embeddings → `EMBEDDINGS_ENABLED=0`
3) Si falla LLM externo → `INTERP_LLM_MODE=off`
4) Si carga alta → `FAQ_ONLY_MODE=1`
