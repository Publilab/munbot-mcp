from typing import Optional, Dict

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    # Fallback para pruebas locales
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[^\w\s]', '', text)
        return text

class DocumentRouter:
    """
    Identifica el documento o tema principal de una consulta de usuario
    basándose en un mapa de palabras clave.
    """
    def __init__(self, topic_map: Dict[str, str]):
        # Normalizamos las claves del mapa para una comparación robusta
        self.topic_map = {normalize_text(k): v for k, v in topic_map.items()}
        # Creamos una lista ordenada por longitud para evitar coincidencias parciales (ej: "patente" vs "patente de alcoholes")
        self.sorted_keys = sorted(self.topic_map.keys(), key=len, reverse=True)

    def get_document_topic(self, user_input: str) -> Optional[str]:
        """
        Busca una coincidencia de tema en la entrada del usuario.
        Devuelve el nombre del documento asociado si lo encuentra.
        """
        normalized_input = normalize_text(user_input)

        for keyword in self.sorted_keys:
            if keyword in normalized_input:
                return self.topic_map[keyword]
        
        return None

import os
import sys
import json
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# Permite importar 'embeddings' desde el microservicio de RAG
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'services', 'llm_docs-mcp')))
try:
    from embeddings import embed
except Exception:
    # Durante pruebas el módulo puede ser stubbed
    from importlib import import_module
    embed = import_module('embeddings').embed

class SemanticDocumentRouter:
    """Enrutador de documentos basado en similitud semántica."""

    def __init__(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        self.doc_names = list(self.config.keys())
        descriptions = [d.get('description', '') for d in self.config.values()]
        # Precalcular embeddings de las descripciones
        self.description_vectors = embed(descriptions)

    def get_document_topic(self, user_input: str, threshold: float = 0.75) -> Optional[str]:
        vec = embed([user_input])[0]
        sims = cosine_similarity([vec], self.description_vectors)[0]
        best_idx = int(np.argmax(sims))
        if sims[best_idx] >= threshold:
            return self.doc_names[best_idx]
        return None
