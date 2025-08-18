import os
import sys
# Ensure the root directory is on the path so 'embeddings' can be imported when
# running this script from the scripts/ folder.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import json
import hashlib
import logging
import re
import argparse
from qdrant_client import QdrantClient
from qdrant_client.http.models import PointStruct
from embeddings import embed  # Reutilizamos el helper de embeddings


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _norm_spaces(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.strip())


def normalize_item(raw: dict) -> dict:
    """
    Acepta esquemas dispares (FAQ question/answer, Contrib texto:list,
    Ayudas content+metadata) y devuelve el formato mínimo común.
    """
    # Mapear claves en inglés a español si existen
    if "question" in raw or "answer" in raw:
        question = raw.pop("question", None)
        answer = raw.pop("answer", None)
        if question is not None:
            raw["pregunta"] = question
        if answer is not None:
            raw["respuesta"] = answer

    doc = raw.get("doc") or raw.get("metadata", {}).get("doc") or ""
    fuente = (
        raw.get("fuente")
        or raw.get("source")
        or raw.get("metadata", {}).get("fuente")
        or raw.get("metadata", {}).get("source")
        or ""
    )

    tags = raw.get("tags") or raw.get("metadata", {}).get("tags") or []
    tags = [str(t).strip() for t in _as_list(tags)]
    if not tags:
        tags = ["faq"] if ("respuesta" in raw or "pregunta" in raw) else ["documento"]

    alias = raw.get("alias") or raw.get("user_says") or []
    alias = [str(a).strip() for a in _as_list(alias)]

    meta = dict(raw.get("metadata") or {})
    if "pregunta" in raw and "pregunta" not in meta:
        meta["pregunta"] = raw["pregunta"]
    if "respuesta" in raw and "respuesta" not in meta:
        meta["respuesta"] = raw["respuesta"]

    texto = raw.get("texto")
    if isinstance(texto, list):
        texto = "\n\n".join(str(x) for x in texto)

    if not texto:
        if "respuesta" in raw:
            texto = raw["respuesta"]
            if raw.get("user_says"):
                alias += [str(a).strip() for a in _as_list(raw["user_says"])]
        elif "content" in raw:
            texto = raw["content"]

    texto = _norm_spaces(str(texto or ""))

    return {
        "doc": str(doc or "").strip(),
        "texto": texto,
        "fuente": str(fuente or "").strip(),
        "tags": tags,
        "alias": alias,
        "metadata": meta,
    }


def stable_point_id(doc: str, fuente: str, texto: str, meta_id: str = "") -> str:
    base = f"{doc}|{fuente}|{_norm_spaces(texto)}|{meta_id or ''}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuración por defecto desde variables de entorno ---
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "munbot_docs")
DOCS_PATH = os.getenv("DOCS_DIR", "/app/documents")


def _get_embeddings_dim(default: int = 384) -> int:
    raw = os.getenv("EMBEDDINGS_DIM")
    try:
        return int(raw) if raw and raw.strip().isdigit() else default
    except Exception:
        return default


EMBEDDINGS_DIM = _get_embeddings_dim()

# Permite override mediante argumentos CLI
parser = argparse.ArgumentParser(description="Indexa documentos RAG en Qdrant")
# Mantener compatibilidad con versiones previas que usaban --src
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
                            items.append(
                                {
                                    "doc": base_id.replace("_", " "),
                                    "texto": clean_p,
                                    "fuente": filename,
                                    "tags": ["documento"],
                                    "alias": [],
                                    "metadata": {
                                        "tipo_fragmento": "documento_oficial",
                                        "id": base_id,
                                        "id_chunk": f"{base_id}-{idx}",
                                    },
                                }
                            )
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

    logger.info(f"Generando embeddings para {len(all_items)} chunks...")
    vectors = embed([item["texto"] for item in all_items])

    logger.info("Subiendo puntos a Qdrant...")

    docs_to_upload = []
    for item, vector in zip(all_items, vectors):
        if vector is None or not item.get("texto"):
            continue
        if len(vector) != EMBEDDINGS_DIM:
            logger.error(
                f"Vector con tamaño inesperado ({len(vector)}) para doc {item.get('doc')}"
            )
            continue

        meta_id = item.get("metadata", {}).get("id", "")
        point_id = stable_point_id(item["doc"], item["fuente"], item["texto"], meta_id)

        payload = {**item}
        payload["metadata"] = dict(item.get("metadata", {}))
        payload["metadata"]["stable_point_id"] = point_id

        docs_to_upload.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )
        )

    if not docs_to_upload:
        logger.warning("No hay documentos válidos para subir a Qdrant.")
        return

    logger.debug(f"Payload de ejemplo: {docs_to_upload[:1]}")

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=docs_to_upload,
        wait=True,
    )
    logger.info(
        f"✅ Indexación completada. {len(all_items)} puntos subidos a la colección '{COLLECTION_NAME}'."
    )

if __name__ == "__main__":
    main()
