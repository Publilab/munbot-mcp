import json
import os
from typing import Optional, List, Dict, Any

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    # Fallback para pruebas locales si la estructura de paquetes no está disponible
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[^\w\s]', '', text)
        return text

class FAQMatcher:
    """
    Carga y busca respuestas en un archivo JSON de Preguntas Frecuentes.
    """
    def __init__(self, faq_path: str):
        self.faqs: List[Dict[str, Any]] = []
        if not os.path.exists(faq_path):
            raise FileNotFoundError(f"El archivo de FAQ no se encontró en: {faq_path}")
        with open(faq_path, 'r', encoding='utf-8') as f:
            self.faqs = json.load(f)

    def match(self, user_input: str) -> Optional[str]:
        """
        Busca una coincidencia para la entrada del usuario en las FAQs.
        Devuelve la respuesta si encuentra una, de lo contrario None.
        """
        normalized_input = normalize_text(user_input)

        for faq_item in self.faqs:
            # 'pregunta' puede ser una lista de frases gatilladoras
            triggers = faq_item.get("pregunta", [])
            for trigger in triggers:
                if normalize_text(trigger) == normalized_input:
                    return faq_item.get("respuesta")
        return None