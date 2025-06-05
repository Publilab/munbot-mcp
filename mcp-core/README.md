# MCP Core

This directory contains the core logic for the Modular Conversational Platform (MCP).

## Structure
- `orchestrator.py`: Main LLM-based orchestrator for intent recognition and tool invocation.
- `tool_schemas/`: JSON schemas describing each microservice's API as callable tools.
- `prompts/`: Prompt templates for the LLM to guide tool usage and conversation flow.

## Next Steps
- Implement the orchestrator logic in `orchestrator.py`.
- Define tool schemas for each microservice in `tool_schemas/`.
- Write prompt templates in `prompts/` for common user intents.