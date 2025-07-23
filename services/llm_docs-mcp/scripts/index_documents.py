import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import json
import uuid
import logging
import re
from qdrant_client import QdrantClient, models
from embeddings import embed  # Reutilizamos el helper de embeddings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuración ---
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = "munbot_docs"

DOCS_PATH = "/app/documents"


def load_rag_json_chunks(directory: str) -> list[dict]:
    """Carga todos los archivos RAG-*.json y los convierte en chunks."""
    chunks = []
    for filename in os.listdir(directory):
        # Incluir todos los JSON que parecen contener datos estructurados para RAG
        if filename.startswith("RAG-") and filename.endswith(".json"):
            filepath = os.path.join(directory, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    # Crear un texto semánticamente rico para el embedding
                    if 'pregunta' in item and 'respuesta' in item:
                        text_to_embed = f"Pregunta frecuente: {item['pregunta']}\nRespuesta: {item['respuesta']}"
                        metadata_text = item['respuesta']
                        fuente = f"FAQ-{item.get('categoria', 'general')}"
                        doc_name = "Preguntas Frecuentes"
                    else:
                        text_to_embed = f"Título: {item.get('titulo', '')}\nContenido: {item.get('texto', '')}"
                        metadata_text = item.get('texto', '')
                        fuente = item.get("fuente", filename)
                        doc_name = item.get("doc", "Desconocido")

                    metadata = {
                        "fuente": fuente,
                        "doc": doc_name,
                        "texto": metadata_text,
                        "tipo_fragmento": item.get("tipo_fragmento", "general")
                    }
                    if "articulo" in item: metadata["articulo"] = item["articulo"]
                    if "decreto" in item: metadata["decreto"] = item["decreto"]

                    chunks.append({
                        "id": item.get("id", str(uuid.uuid4())),
                        "text": text_to_embed,
                        "metadata": metadata
                    })
            except Exception as e:
                logger.error(f"Error cargando archivo RAG JSON {filename}: {e}")
    return chunks


def load_text_file_chunks(directory: str) -> list[dict]:
    """Carga archivos .txt recursivamente, los divide en párrafos y prepara para indexar."""
    chunks = []
    for root, _, files in os.walk(directory):
        for filename in files:
            if filename.endswith(".txt"):
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    paragraphs = re.split(r'\n\s*\n', content)
                    for p in paragraphs:
                        clean_p = p.strip()
                        if clean_p:
                            chunks.append({
                                "id": str(uuid.uuid4()),
                                "text": clean_p,
                                "metadata": {
                                    "fuente": filename,
                                    "doc": os.path.splitext(filename)[0].replace("_", " "),
                                    "texto": clean_p,
                                    "tipo_fragmento": "documento_oficial"
                                }
                            })
                except Exception as e:
                    logger.error(f"Error procesando archivo de texto {filename}: {e}")
    return chunks


def main():
    """Función principal para indexar todos los documentos en Qdrant."""
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    try:
        count = client.count(collection_name=COLLECTION_NAME, exact=True).count
        if count > 0:
            logger.info(f"ℹ️ La colección '{COLLECTION_NAME}' ya contiene {count} puntos. Saltando indexación.")
            return
    except Exception:
        logger.info(f"No se pudo verificar el conteo de la colección '{COLLECTION_NAME}'. Procediendo con la indexación.")

    logger.info("Cargando y procesando documentos...")
    json_chunks = load_rag_json_chunks(DOCS_PATH)
    text_chunks = load_text_file_chunks(DOCS_PATH)
    all_chunks = json_chunks + text_chunks

    if not all_chunks:
        logger.warning("No se encontraron documentos para indexar. Saliendo.")
        return

    logger.info(f"Generando embeddings para {len(all_chunks)} chunks...")
    vectors = embed([chunk["text"] for chunk in all_chunks])

    logger.info("Subiendo puntos a Qdrant...")
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            models.PointStruct(id=str(chunk["id"]), vector=vector, payload=chunk["metadata"])
            for chunk, vector in zip(all_chunks, vectors)
        ],
        wait=True,
    )
    logger.info(f"✅ Indexación completada. {len(all_chunks)} puntos subidos a la colección '{COLLECTION_NAME}'.")

if __name__ == "__main__":
    main()