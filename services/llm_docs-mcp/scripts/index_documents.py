import os
import sys
import json
import logging
import re
import argparse
import hashlib
from typing import Any, Dict, Iterable

# Ensure the root directory is on the path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct # Import PointStruct
from embeddings import embed
from qdrant_helpers import EMBEDDINGS_DIM # Import EMBEDDINGS_DIM from qdrant_helpers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import qdrant_client
    logger.info(f"Qdrant client version: {qdrant_client.__version__}")
except (ImportError, AttributeError):
    logger.info("Could not determine Qdrant client version.")

# --- Helper functions ---
def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def _norm_spaces(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip())

def normalize_item(item: Dict[str, Any], filename: str) -> Dict[str, Any]:
    # 1) Texto robusto
    if "texto" in item:
        t = item["texto"]
        texto = " ".join(t) if isinstance(t, list) else str(t)
    elif "content" in item:
        c = item["content"]
        texto = " ".join(c) if isinstance(c, list) else str(c)
    elif "answer" in item or "respuesta" in item:
        # Soporta FAQ antiguos (question/answer) o (pregunta/respuesta)
        q = item.get("question") or item.get("pregunta") or ""
        a = item.get("answer") or item.get("respuesta") or ""
        texto = f"Pregunta frecuente: {q}\nRespuesta: {a}".strip()
    else:
        # Último recurso: concatena strings sueltos
        texto = " ".join(str(v) for v in item.values() if isinstance(v, str))

    # 2) Doc / Fuente
    meta = item.get("metadata") or {}
    doc = item.get("doc") or meta.get("source_doc") or meta.get("doc") or "desconocido"
    fuente = item.get("fuente") or filename

    # 3) Tags / Alias (listas)
    tags = item.get("tags") or meta.get("tags") or []
    if isinstance(tags, str): tags = [tags]
    alias = item.get("alias") or meta.get("alias") or []
    if isinstance(alias, str): alias = [alias]

    # 4) Metadata mínima coherente
    metadata = {
        "category": meta.get("category"),
        "subcategory": meta.get("subcategory"),
        "id": meta.get("id"),
        "id_chunk": meta.get("id_chunk"),
        "title": meta.get("title"),
        "source_doc": meta.get("source_doc") or doc,
        "raw": meta.get("raw"),  # opcional
    }

    return {
        "doc": doc, "texto": _norm_spaces(texto), "fuente": fuente,
        "tags": tags, "alias": alias, "metadata": metadata
    }

def stable_point_id(n: Dict[str, Any]) -> str:
    base = f"{n['doc']}|{n['fuente']}|{n['texto']}|{(n['metadata'] or {}).get('id','')}"
    return _sha1(base)

# --- Configuración por defecto desde variables de entorno ---
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "munbot_docs")
DOCS_PATH = os.getenv("DOCS_DIR", "/app/documents")

# Permite override mediante argumentos CLI
parser = argparse.ArgumentParser(description="Indexa documentos RAG en Qdrant")
parser.add_argument("--docs-dir", "--src", dest="docs_dir", default=DOCS_PATH)
parser.add_argument("--collection", default=COLLECTION_NAME)
args = parser.parse_args()
DOCS_PATH = args.docs_dir
COLLECTION_NAME = args.collection

def load_rag_json_chunks(directory: str) -> list[dict]:
    """Carga todos los archivos RAG-*.json y normaliza cada entrada."""
    items: list[dict] = []
    for filename in os.listdir(directory):
        if filename.startswith("RAG-") and filename.endswith(".json"):
            filepath = os.path.join(directory, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"Error cargando archivo RAG JSON {filename}: {e}")
                data = None
            if not data:
                continue
            for raw in data:
                item = normalize_item(raw, filename) # Pass filename to normalize_item
                if item:
                    items.append(item)
    return items

def load_text_file_chunks(directory: str) -> list[dict]:
    """Carga archivos .txt recursivamente y los convierte al formato normalizado."""
    items: list[dict] = []
    for root, _, files in os.walk(directory):
        for filename in files:
            if filename.endswith(".txt"):
                filepath = os.path.join(root, filename)
                base_id = os.path.splitext(filename)[0]
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    paragraphs = re.split(r'\n\s*\n', content)
                    for idx, p in enumerate(paragraphs):
                        clean_p = _norm_spaces(p)
                        if clean_p:
                            # Use the new normalize_item for text chunks as well
                            raw_item = {
                                "doc": base_id.replace("_", " "),
                                "texto": clean_p,
                                "fuente": filename,
                                "tags": ["documento"],
                                "alias": [],
                                "metadata": {
                                    "tipo_fragmento": "documento_oficial",
                                    "id": base_id,
                                    "id_chunk": f"{base_id}-{idx}",
                                    "doc": base_id.replace("_", " "),
                                    "alias": [],
                                    "tags": ["documento"],
                                },
                            }
                            item = normalize_item(raw_item, filename)
                            if item:
                                items.append(item)
                except Exception as e:
                    logger.error(f"Error procesando archivo de texto {filename}: {e}")
    return items

def main():
    """Función principal para indexar todos los documentos en Qdrant."""
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    try:
        count = client.count(collection_name=COLLECTION_NAME, exact=True).count
        if count > 0:
            logger.info(
                f"ℹ️ La colección '{COLLECTION_NAME}' ya contiene {count} puntos. Reindexando para actualizar o insertar nuevos datos."
            )
    except Exception:
        logger.info(f"No se pudo verificar el conteo de la colección '{COLLECTION_NAME}'. Procediendo con la indexación.")

    logger.info("Cargando y procesando documentos...")
    json_items = load_rag_json_chunks(DOCS_PATH)
    text_items = load_text_file_chunks(DOCS_PATH)
    all_items = json_items + text_items

    if not all_items:
        logger.warning("No se encontraron documentos para indexar. Saliendo.")
        return

    logger.info(f"Generando embeddings y subiendo puntos a Qdrant para {len(all_items)} chunks...")

    # Embeddings
    texts = [item["texto"] for item in all_items]
    embeddings = embed(texts)

    # Build PointStruct list
    points: list[PointStruct] = []
    for n, vec in zip(all_items, embeddings):
        # Ensure vector is a list of floats
        vec = [float(x) for x in vec]

        pid = stable_point_id(n)
        payload = {
            "doc": n["doc"],
            "texto": n["texto"],
            "fuente": n["fuente"],
            "tags": n["tags"],
            "alias": n["alias"],
            "metadata": n["metadata"],
        }
        points.append(PointStruct(id=pid, vector=vec, payload=payload))

    # Upsert with list of PointStruct
    client.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)

    logger.info(
        f"✅ Indexación completada. {len(all_items)} puntos procesados para la colección '{COLLECTION_NAME}'."
    )

if __name__ == "__main__":
    main()
