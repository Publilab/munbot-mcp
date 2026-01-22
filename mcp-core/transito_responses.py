"""
Response Builder for Transit Department
Loads KB JSONs and constructs formatted responses for matched intents
"""

import json
import os
import glob
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

@dataclass
class AspectResponse:
    """A specific aspect of a response (e.g., requisitos, costos)"""
    aspect_name: str
    content: Any
    formatted: str

@dataclass
class TransitoResponse:
    """Complete response for a transit query"""
    intent_id: str
    tramite_name: str
    main_response: str
    aspects: Dict[str, AspectResponse]
    fuentes: List[str]
    bordes: List[str]
    aspect_buttons: List[str]
    intent_type: str

class TransitoResponses:
    """
    Response builder that loads knowledge base and constructs responses.
    """
    
    # Mapping of aspect keywords to JSON field names
    ASPECT_MAPPING = {
        'donde': ['donde', 'dónde', 'lugar', 'ubicación'],
        'requisitos': ['requisitos', 'que necesito', 'qué necesito', 'documentos', 'papeles'],
        'costos': ['costo', 'costos', 'precio', 'cuánto', 'cuanto', 'valor'],
        'plazos': ['plazo', 'plazos', 'tiempo', 'demora', 'cuánto demora'],
        'horarios': ['horario', 'horarios', 'hora', 'cuando', 'cuándo'],
        'documentacion': ['documentación', 'documentacion', 'papeles', 'antecedentes'],
        'instrucciones_app': ['agendar', 'reservar', 'cita', 'turno', 'hora'],
        'proposito': ['para qué', 'para que', 'sirve', 'habilita'],
    }
    
    def __init__(self, kb_directory: str = None):
        """
        Initialize response builder with KB directory.
        
        Args:
            kb_directory: Path to docs/Transito/Json folder
        """
        if kb_directory is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            kb_directory = os.path.join(base_dir, "docs", "Transito", "Json")
        
        self.kb_directory = kb_directory
        self.knowledge_base: Dict[str, Dict] = {}  # intent_id -> full record
        self._load_knowledge_base()
    
    def _load_knowledge_base(self):
        """Load all KB JSON files into memory"""
        json_files = glob.glob(os.path.join(self.kb_directory, "*.json"))
        
        for file_path in json_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                tramites = data.get('tramites', [])
                for tramite in tramites:
                    intent_id = tramite.get('id')
                    if intent_id:
                        self.knowledge_base[intent_id] = tramite
                        
            except Exception as e:
                print(f"[TransitoResponses] Error loading {file_path}: {e}")
        
        print(f"[TransitoResponses] Loaded {len(self.knowledge_base)} intents from KB")
    
    def get_response(self, intent_id: str, user_query: str = None) -> Optional[TransitoResponse]:
        """
        Build response for a matched intent.
        
        Args:
            intent_id: The matched intent ID
            user_query: Original user query (to detect specific aspects)
            
        Returns:
            TransitoResponse with formatted content
        """
        if intent_id not in self.knowledge_base:
            return None
        
        record = self.knowledge_base[intent_id]
        respuestas = record.get('respuestas', {})
        
        # Detect which aspect the user is asking about
        detected_aspect = self._detect_aspect(user_query) if user_query else None
        
        # Build main response
        main_response = self._build_main_response(record, detected_aspect)
        
        # Build aspect dictionary
        aspects = self._build_aspects(respuestas)
        
        # Get tramite name from aliases or ID
        tramite_name = record.get('aliases', [intent_id])[0] if record.get('aliases') else intent_id
        
        # Determine intent type
        intent_type = 'agenda' if record.get('tipo_atencion') == 'agenda_oficial' else 'faq'
        
        return TransitoResponse(
            intent_id=intent_id,
            tramite_name=tramite_name,
            main_response=main_response,
            aspects=aspects,
            fuentes=record.get('fuentes', []),
            bordes=record.get('bordes', []),
            aspect_buttons=record.get('aspect_buttons', []),
            intent_type=intent_type
        )
    
    def _detect_aspect(self, user_query: str) -> Optional[str]:
        """Detect which specific aspect the user is asking about"""
        if not user_query:
            return None
        
        query_lower = user_query.lower()
        
        for aspect, keywords in self.ASPECT_MAPPING.items():
            if any(kw in query_lower for kw in keywords):
                return aspect
        
        return None
    
    def _build_main_response(self, record: Dict, detected_aspect: str = None) -> str:
        """Build the main response text"""
        respuestas = record.get('respuestas', {})
        
        # If specific aspect detected, return that
        if detected_aspect and detected_aspect in respuestas:
            content = respuestas[detected_aspect]
            return self._format_content(content)
        
        # For agenda type, return instructions
        if record.get('tipo_atencion') == 'agenda_oficial':
            instrucciones = respuestas.get('instrucciones_app', [])
            if instrucciones:
                return "Para agendar tu hora:\n" + "\n".join(f"• {i}" for i in instrucciones)
            return respuestas.get('derivacion', respuestas.get('donde', 'Consulta los canales oficiales.'))
        
        # Default: return 'donde' or first available response
        if 'donde' in respuestas:
            return respuestas['donde']
        
        # Fallback: combine definition with first available info
        definicion = record.get('definicion_licencia', record.get('definicion', []))
        if definicion:
            return " ".join(definicion) if isinstance(definicion, list) else definicion
        
        return "Información disponible. ¿Qué aspecto te interesa conocer?"
    
    def _format_content(self, content: Any) -> str:
        """Format content for display"""
        if isinstance(content, str):
            return content
        
        if isinstance(content, list):
            return "\n".join(f"• {item}" for item in content)
        
        if isinstance(content, dict):
            # Handle nested dicts (like requisitos.generales, requisitos.especiales)
            parts = []
            for key, value in content.items():
                if isinstance(value, list):
                    parts.append(f"**{key.replace('_', ' ').title()}:**")
                    parts.extend(f"  • {item}" for item in value)
                else:
                    parts.append(f"**{key.replace('_', ' ').title()}:** {value}")
            return "\n".join(parts)
        
        return str(content)
    
    def _build_aspects(self, respuestas: Dict) -> Dict[str, AspectResponse]:
        """Build dictionary of all available aspects"""
        aspects = {}
        
        for key, value in respuestas.items():
            formatted = self._format_content(value)
            aspects[key] = AspectResponse(
                aspect_name=key,
                content=value,
                formatted=formatted
            )
        
        return aspects
    
    def format_for_chat(self, response: TransitoResponse, include_buttons: bool = True) -> str:
        """
        Format response for chat display.
        
        Args:
            response: TransitoResponse object
            include_buttons: Whether to include suggested action buttons
            
        Returns:
            Formatted string for chat
        """
        lines = []
        
        # Main response
        lines.append(response.main_response)
        
        # Source citation
        if response.fuentes:
            lines.append("")
            lines.append(f"📚 *Fuente: {', '.join(response.fuentes)}*")
        
        # Suggested buttons
        if include_buttons and response.aspect_buttons:
            lines.append("")
            lines.append("¿Te gustaría saber más sobre?")
            for btn in response.aspect_buttons[:4]:  # Max 4 buttons
                lines.append(f"  🔘 {btn}")
        
        return "\n".join(lines)


# Singleton
_responses_instance: Optional[TransitoResponses] = None

def get_responses() -> TransitoResponses:
    """Get or create singleton instance"""
    global _responses_instance
    if _responses_instance is None:
        _responses_instance = TransitoResponses()
    return _responses_instance


# CLI for testing
if __name__ == "__main__":
    from transito_router import TransitoRouter
    
    router = TransitoRouter()
    responses = TransitoResponses()
    
    test_queries = [
        "cuales son los requisitos para licencia A1",
        "cuanto cuesta la licencia profesional A2",
        "quiero agendar una hora para renovar",
        "que cubre el soap"
    ]
    
    print("=" * 60)
    print("TRANSITO RESPONSE BUILDER - TEST")
    print("=" * 60)
    
    for query in test_queries:
        print(f"\n📝 Query: \"{query}\"")
        print("-" * 40)
        
        match = router.match(query)
        if match:
            response = responses.get_response(match.intent_id, query)
            if response:
                formatted = responses.format_for_chat(response)
                print(formatted)
            else:
                print("❌ No response found in KB")
        else:
            print("❌ No intent matched")
