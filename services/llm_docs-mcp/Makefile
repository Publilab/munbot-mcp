# Makefile para llm_docs-mcp
# Ubica este archivo en: munbot-docker/services/llm_docs-mcp/Makefile

.PHONY: run test lint build tags clean

# Corre el servicio FastAPI en modo desarrollo (hot-reload)
run:
	uvicorn gateway:app --reload --host 0.0.0.0 --port 8000

# Ejecuta pruebas unitarias y de integración
test:
	pytest tests/

# Linting de estilo con flake8
lint:
	flake8 gateway.py

# Construye la imagen Docker
build:
	docker build -t llm_docs-mcp .

# Genera/actualiza metadata de tags automáticamente con tu script
tags:
	python generate_tags.py

# Limpia archivos pyc y logs antiguos
clean:
	find . -name "*.pyc" -delete
	rm -f gateway.log

# Muestra los comandos disponibles (ayuda rápida)
help:
	@echo "Comandos disponibles:"
	@echo "  make run     - Corre el servicio FastAPI en modo desarrollo"
	@echo "  make test    - Ejecuta los tests"
	@echo "  make lint    - Chequea el estilo de código"
	@echo "  make build   - Construye la imagen Docker"
	@echo "  make tags    - Genera/actualiza tags automáticos"
	@echo "  make clean   - Limpia archivos pyc y logs"
