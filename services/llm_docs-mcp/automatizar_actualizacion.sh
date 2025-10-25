#!/bin/bash
# Script de Publilab Consulting para automatizar procesamiento de documentos
# Uso: bash automatizar_actualizacion.sh

set -e

# 1. Procesar nuevos documentos PDF/TXT
echo "Procesando documentos..."
python3 process_documents.py --input_dir documents/raw --output_dir documents/clean

echo "Documentos procesados y limpios en documents/clean."

echo "Automatización completada. Recuerda reiniciar el servicio si el modelo fue actualizado."