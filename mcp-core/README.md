# MCP Core

This directory contains the core logic for the Modular Conversational Platform (MCP).

## Structure
- `orchestrator.py`: Main LLM-based orchestrator for intent recognition and tool invocation.
- It also defines a minimal `STOPWORDS` list used for tokenization. Duplicates and
  rarely helpful adverbs were removed to avoid filtering meaningful tokens.
- `tool_schemas/`: JSON schemas describing each microservice's API as callable tools.
- `prompts/`: Prompt templates for the LLM to guide tool usage and conversation flow.

## Next Steps
- Implement the orchestrator logic in `orchestrator.py`.
- Define tool schemas for each microservice in `tool_schemas/`.
 - Write prompt templates in `prompts/` for common user intents.

## Documento FAQ Helper
The function `responder_sobre_documento` can answer questions about specific
documents stored in `databases/documento_requisito.json`. Besides the classic
fields (`requisitos`, `horario`, `correo` and `dirección`), it also recognizes
queries about:

- **Teléfono** (`telefono`)
- **Vigencia** (`tiempo_validez`)
- **Utilidad** (`utilidad`)
- **Penalidad** (`penalidad`)
- **Costo** (`costo` when available)

Example queries:

```
¿Para qué sirve el Certificado de Residencia?
¿Cuál es la vigencia de la Licencia de Transporte Espacial?
¿Teléfono de contacto para el Certificado Registro de Carga?
```
