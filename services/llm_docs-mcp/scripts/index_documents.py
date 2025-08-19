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
from qdrant_client.http.models import Batch
from embeddings import embed  # Reutilizamos el helper de embeddings


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

    ids = []
    vectors_to_upload = []
    payloads = []
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
        
        ids.append(point_id)
        vectors_to_upload.append(vector)
        payloads.append(payload)

    if not ids:
        logger.warning("No hay documentos válidos para subir a Qdrant.")
        return

    logger.debug(f"Payload de ejemplo: {payloads[:1]}")

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=Batch(
            ids=ids,
            vectors=vectors_to_upload,
            payloads=payloads
        ),
        wait=True,
    )
    logger.info(
        f"✅ Indexación completada. {len(all_items)} puntos subidos a la colección '{COLLECTION_NAME}'."
    )

if __name__ == "__main__":
    main()