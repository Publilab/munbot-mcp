import os
import sys
import json
import logging
import re
import argparse
import hashlib
import httpx
from typing import Any, Dict, Iterable

# Ensure the root directory is on the path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qdrant_client import QdrantClient
from embeddings import embed

# --- Constants with Environment Variable Fallbacks ---
EMBEDDINGS_DIM = int(os.getenv("EMBEDDINGS_DIM", "384"))
Q_HOST = os.getenv("QDRANT_HOST", "qdrant")
Q_PORT = int(os.getenv("QDRANT_PORT", "6333"))
Q_COLL = os.getenv("QDRANT_COLLECTION", "munbot_docs")
Q_TIMEOUT = float(os.getenv("QDRANT_TIMEOUT", "10"))


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

def _get_collection_dim() -> int | None:
    """Fetches the vector dimension from an existing Qdrant collection."""
    try:
        r = httpx.get(f"http://{Q_HOST}:{Q_PORT}/collections/{Q_COLL}", timeout=Q_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # Qdrant >=1.8: vectors can be a dict of named vectors or a single vector config object
        vectors_config = data["result"].get("vectors", {})
        
        if isinstance(vectors_config, dict):  # Named vectors
            # If multiple named vectors exist, take the first one as reference.
            first_vector_config = next(iter(vectors_config.values()), None)
            if first_vector_config and "size" in first_vector_config:
                return int(first_vector_config["size"])
        elif hasattr(vectors_config, 'size'): # Single vector
             return int(vectors_config.get("size"))

    except Exception as e:
        logger.warning(f"Could not determine dimension for collection '{Q_COLL}': {e}")
        return None
    return None

def _valid_vec(v):
    """Validates a vector to ensure it's well-formed."""
    return (
        isinstance(v, (list, tuple)) and
        len(v) == EMBEDDINGS_DIM and
        all(isinstance(x, (float, int)) and x == x and x not in (float("inf"), float("-inf")) for x in v)
    )

def normalize_item(item: Dict[str, Any], filename: str = "") -> Dict[str, Any]:
    # Normaliza llaves en español si vienen como question/answer y actualiza metadata
    q = item.pop("question", None)
    a = item.pop("answer", None)
    if q is not None:
        item["pregunta"] = q
    if a is not None:
        item["respuesta"] = a

    meta = item.get("metadata") or {}
    if q is not None:
        meta["pregunta"] = q
    if a is not None:
        meta["respuesta"] = a
    item["metadata"] = meta

    # 1) Texto robusto
    if "texto" in item:
        t = item["texto"]
        texto = " ".join(t) if isinstance(t, list) else str(t)
    elif "content" in item:
        c = item["content"]
        texto = " ".join(c) if isinstance(c, list) else str(c)
    elif "respuesta" in item or a is not None:
        texto = item.get("respuesta") or a or ""
    else:
        # Último recurso: concatena strings sueltos
        texto = " ".join(str(v) for v in item.values() if isinstance(v, str))

    # 2) Doc / Fuente
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
        "pregunta": meta.get("pregunta"),
        "respuesta": meta.get("respuesta"),
        "tema": meta.get("tema"),
        "tipo_fragmento": meta.get("tipo_fragmento"),
        "seccion": meta.get("seccion"),
        "priority": meta.get("priority"),
        "faq_id": meta.get("faq_id"),
        # opcionales adicionales
        "nivel_normativo": meta.get("nivel_normativo"),
        "peso_normativo": meta.get("peso_normativo"),
        "vigencia_inicio": meta.get("vigencia_inicio"),
        "vigencia_fin": meta.get("vigencia_fin"),
        "version": meta.get("version"),
        "last_updated": meta.get("last_updated"),
    }

    return {
        "doc": doc, "texto": _norm_spaces(texto), "fuente": fuente,
        "tags": tags, "alias": alias, "metadata": metadata
    }

from uuid import uuid5, NAMESPACE_URL

def stable_uuid_for_chunk(n):
    base = f"{n['doc']}|{n['alias']}|{n['metadata'].get('page', 0)}|{n['texto'][:64]}"
    return str(uuid5(NAMESPACE_URL, base))

def to_float_list(vec):
    # Convierte numpy arrays / float32 a float nativo de Python
    try:
        # Si es numpy array
        vec = vec.tolist()
    except AttributeError:
        pass
    return [float(x) for x in vec]

def make_payload(n):
    # Solo tipos JSON (str, int, float, bool, None, list, dict anidados)
    return {
        "doc": n.get("doc"),
        "texto": n.get("texto"),
        "fuente": n.get("fuente"),
        "tags": n.get("tags"),
        "alias": n.get("alias"),
        "metadata": n.get("metadata"),
    }

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
    metadata_path = os.path.join(directory, "metadata.json")
    metadata_map: dict[str, dict] = {}
    if os.path.isfile(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata_map = json.load(f)
        except Exception as e:
            logger.error(f"Error cargando metadata global: {e}")

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

            global_meta = metadata_map.get(filename, {})
            for raw in data:
                raw_meta = raw.get("metadata") or {}
                for k in [
                    "nivel_normativo",
                    "peso_normativo",
                    "vigencia_inicio",
                    "vigencia_fin",
                    "version",
                    "last_updated",
                ]:
                    if k in global_meta and k not in raw_meta:
                        raw_meta[k] = global_meta[k]
                raw["metadata"] = raw_meta

                item = normalize_item(raw, filename)
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

    # Validate collection dimension before proceeding
    _coll_dim = _get_collection_dim()
    if _coll_dim is not None and _coll_dim != EMBEDDINGS_DIM:
        raise RuntimeError(
            f"Dimensión de colección Qdrant ({_coll_dim}) != EMBEDDINGS_DIM ({EMBEDDINGS_DIM}). "
            f"Recrea la colección o ajusta EMBEDDINGS_DIM/tu modelo de embeddings."
        )

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

    points = []
    for n, vec in zip(all_items, embeddings):
        pid = stable_uuid_for_chunk(n)
        payload = make_payload(n)
        vector = to_float_list(vec)

        # Sanidad local (opcional pero muy útil)
        assert isinstance(pid, (int, str)), f"id inválido: {type(pid)}"
        if isinstance(pid, str):
            import uuid as _uuid
            _ = _uuid.UUID(pid)  # valida formato UUID; lanzará excepción si es inválido
        assert isinstance(vector, list) and all(isinstance(x, float) for x in vector), "vector debe ser list[float]"

        points.append({
            "id": pid,
            "vector": vector,
            "payload": payload,
        })

    # (Opcional) prueba de serialización local para detectar tipos raros:
    import json
    json.dumps({"points": points[:1]})  # no debe lanzar excepción

    # Upsert with list of dictionaries
    client.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)

    logger.info(
        f"✅ Indexación completada. {len(all_items)} puntos procesados para la colección '{COLLECTION_NAME}'."
    )


if __name__ == "__main__":
    main()
