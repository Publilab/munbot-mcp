import os
import sys
import json
import logging
import re
import argparse

# Ensure the root directory is on the path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__name__), '..')))

from qdrant_client import QdrantClient
from embeddings import embed


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _pick(*vals):
    for v in vals:
        if v not in (None, "", []):
            return v
    return None


def _norm_spaces(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip())


def normalize_item(raw: dict) -> dict | None:
    """Normaliza un item con formato heterogéneo."""
    # Mapear claves en inglés a español si existen
    if "question" in raw or "answer" in raw:
        question = raw.get("question")
        answer = raw.get("answer")
        if question is not None:
            raw.setdefault("pregunta", question)
        if answer is not None:
            raw.setdefault("respuesta", answer)

    texto_final = _pick(
        raw.get("texto"),
        raw.get("respuesta"),
        raw.get("content"),
        raw.get("titulo"),
    )
    if isinstance(texto_final, list):
        texto_final = "\n\n".join(map(str, texto_final))
    if not texto_final:
        return None

    alias = _as_list(
        _pick(
            raw.get("alias"),
            raw.get("user_says"),
            raw.get("metadata", {}).get("alias"),
        )
    )
    alias = [str(a).strip() for a in alias if str(a).strip()]
    if raw.get("pregunta"):
        alias.append(str(raw["pregunta"]).strip())

    tags = _as_list(
        _pick(
            raw.get("tags"),
            raw.get("metadata", {}).get("tags"),
        )
    )
    tags = [str(t).strip() for t in tags if str(t).strip()]

    doc_logico = _pick(
        raw.get("doc"),
        raw.get("metadata", {}).get("doc"),
        raw.get("id_documento"),
        raw.get("nombre"),
    )
    doc_logico = (doc_logico or "").strip()

    id_logico = _pick(
        raw.get("metadata", {}).get("id"),
        raw.get("id"),
        raw.get("faq_id"),
    )
    id_logico = (id_logico or "").strip()

    tipo_fragmento = _pick(
        raw.get("metadata", {}).get("tipo_fragmento"),
        raw.get("metadata", {}).get("seccion"),
    )

    metadata = dict(raw.get("metadata") or {})
    metadata.setdefault("id", id_logico)
    metadata.setdefault("doc", doc_logico)
    if tipo_fragmento:
        metadata.setdefault("tipo_fragmento", tipo_fragmento)
    metadata["alias"] = alias
    metadata["tags"] = tags

    return {
        "doc": doc_logico,
        "texto": _norm_spaces(str(texto_final)),
        "fuente": _pick(raw.get("fuente"), raw.get("source")) or "",
        "alias": alias,
        "tags": tags,
        "metadata": metadata,
    }

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import qdrant_client
    logger.info(f"Qdrant client version: {qdrant_client.__version__}")
except (ImportError, AttributeError):
    logger.info("Could not determine Qdrant client version.")

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
                item = normalize_item(raw)
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
                            doc_name = base_id.replace("_", " ")
                            items.append(
                                {
                                    "doc": doc_name,
                                    "texto": clean_p,
                                    "fuente": filename,
                                    "tags": ["documento"],
                                    "alias": [],
                                    "metadata": {
                                        "tipo_fragmento": "documento_oficial",
                                        "id": base_id,
                                        "id_chunk": f"{base_id}-{idx}",
                                        "doc": doc_name,
                                        "alias": [],
                                        "tags": ["documento"],
                                    },
                                }
                            )
                except Exception as e:
                    logger.error(f"Error procesando archivo de texto {filename}: {e}")
    return items



import hashlib
import math
from qdrant_client.http.models import PointStruct

def make_stable_id(item: dict) -> str:
    # Normaliza el texto antes de hashear para evitar duplicados por espacios
    doc = str(item.get("doc", "")).strip()
    texto = str(item.get("texto", "")).strip()
    texto_norm = re.sub(r"\s+", " ", texto)
    base = (doc + "|" + texto_norm).encode("utf-8")
    return hashlib.sha1(base).hexdigest()

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

    # Validación de vectores
    if not embeddings or any(len(vec) != len(embeddings[0]) for vec in embeddings):
        logger.error("Error en la generación de embeddings o dimensiones inconsistentes.")
        return

    embedding_dim = len(embeddings[0])

    # Verificar dimensión de la colección en Qdrant
    try:
        collection_info = client.get_collection(COLLECTION_NAME)
        qdrant_dim = collection_info.vector_size
        if qdrant_dim != embedding_dim:
            logger.error(f"Dimensión de la colección Qdrant ({qdrant_dim}) no coincide con la de los embeddings ({embedding_dim}). Aborta indexación.")
            return
    except Exception as e:
        logger.warning(f"No se pudo obtener la dimensión de la colección en Qdrant: {e}. Se asume que es compatible.")

    for idx, vec in enumerate(embeddings):
        if not isinstance(vec, (list, tuple)):
            logger.error(f"Embedding #{idx} no es lista/tupla: {type(vec)}")
            return
        if len(vec) != embedding_dim:
            logger.error(f"Embedding #{idx} tiene dimensión {len(vec)} en vez de {embedding_dim}")
            return
        for j, val in enumerate(vec):
            if not isinstance(val, (float, int)):
                logger.error(f"Embedding #{idx} posición {j} no es float/int: {type(val)}")
                return
            if math.isnan(val) or math.isinf(val):
                logger.error(f"Embedding #{idx} posición {j} es NaN o Inf: {val}")
                return

    # Construir puntos
    import json
    points = []
    for item, vector in zip(all_items, embeddings):
        point_id = make_stable_id(item)
        payload = {k: v for k, v in item.items() if k != "texto"}
        payload["texto"] = item["texto"]
        # Asegura que el payload sea JSON-serializable
        try:
            json.dumps(payload, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Payload no serializable para ID {point_id}: {e}\nPayload: {payload}")
            continue
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload=payload
            )
        )

    # Upsert directo de la lista de puntos (sin batch wrapper extra)
    try:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
        logger.info(f"✅ Indexación completada. {len(points)} puntos procesados para la colección '{COLLECTION_NAME}'.")
    except Exception as e:
        logger.error(f"❌ Error al hacer upsert en Qdrant: {e}")

if __name__ == "__main__":
    main()
