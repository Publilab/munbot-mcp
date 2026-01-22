"""
Transit Handler - Entry point for Transit department queries
Combines Router + Response Builder into a single interface
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from transito_router import TransitoRouter, MatchResult, get_router
from transito_responses import TransitoResponses, TransitoResponse, get_responses


@dataclass
class TransitResult:
    """Complete result from Transit department handler"""
    success: bool
    intent_id: Optional[str]
    intent_type: Optional[str]  # 'faq' or 'agenda'
    confidence: float
    response_text: str
    suggested_buttons: list
    fuentes: list
    needs_clarification: bool
    alternatives: list  # For disambiguation


class TransitHandler:
    """
    Main handler for Transit department.
    Call this from the orchestrator to process transit-related queries.
    """
    
    CONFIDENCE_HIGH = 0.85  # Respond directly
    CONFIDENCE_MEDIUM = 0.70  # Respond but offer alternatives
    CONFIDENCE_LOW = 0.50  # Ask for clarification
    
    def __init__(self):
        self.router = get_router()
        self.responses = get_responses()
    
    def handle(self, user_message: str) -> TransitResult:
        """
        Process a user message and return transit response.
        
        Args:
            user_message: The user's query
            
        Returns:
            TransitResult with response and metadata
        """
        # First check if this is even a transit query
        if not self.router.is_transit_query(user_message):
            return TransitResult(
                success=False,
                intent_id=None,
                intent_type=None,
                confidence=0.0,
                response_text="",
                suggested_buttons=[],
                fuentes=[],
                needs_clarification=False,
                alternatives=[]
            )
        
        # Try to match intent
        match = self.router.match(user_message)
        
        if not match:
            # No confident match - provide helpful fallback
            return TransitResult(
                success=False,
                intent_id=None,
                intent_type=None,
                confidence=0.0,
                response_text="No encontré información específica sobre tu consulta. ¿Podrías reformularla o indicar si es sobre licencias, permisos de circulación, multas TAG o SOAP?",
                suggested_buttons=["Licencias", "Permisos", "Multas TAG", "SOAP"],
                fuentes=[],
                needs_clarification=True,
                alternatives=self._get_suggestions(user_message)
            )
        
        # Get full response
        response = self.responses.get_response(match.intent_id, user_message)
        
        if not response:
            return TransitResult(
                success=True,
                intent_id=match.intent_id,
                intent_type=match.intent_type,
                confidence=match.confidence,
                response_text=f"Detecté tu consulta sobre '{match.intent_id}', pero no tengo información detallada en este momento.",
                suggested_buttons=[],
                fuentes=[],
                needs_clarification=False,
                alternatives=[]
            )
        
        # Format response for chat
        formatted_response = self.responses.format_for_chat(response)
        
        # Check if we should offer alternatives (medium confidence)
        alternatives = []
        if match.confidence < self.CONFIDENCE_HIGH:
            alternatives = self._get_suggestions(user_message, exclude_id=match.intent_id)
        
        return TransitResult(
            success=True,
            intent_id=match.intent_id,
            intent_type=match.intent_type,
            confidence=match.confidence,
            response_text=formatted_response,
            suggested_buttons=response.aspect_buttons[:4],
            fuentes=response.fuentes,
            needs_clarification=match.confidence < self.CONFIDENCE_MEDIUM,
            alternatives=alternatives
        )
    
    def _get_suggestions(self, user_message: str, exclude_id: str = None, limit: int = 3) -> list:
        """Get alternative suggestions for disambiguation"""
        top_matches = self.router.get_top_matches(user_message, 5)
        
        suggestions = []
        for intent_id, conf, text in top_matches:
            if intent_id != exclude_id and conf > 0.5:
                suggestions.append({
                    'intent_id': intent_id,
                    'confidence': conf,
                    'sample': text[:50]
                })
                if len(suggestions) >= limit:
                    break
        
        return suggestions
    
    def to_dict(self, result: TransitResult) -> Dict[str, Any]:
        """Convert result to dictionary for JSON serialization"""
        return asdict(result)


# Singleton
_handler_instance: Optional[TransitHandler] = None

def get_handler() -> TransitHandler:
    """Get or create singleton handler"""
    global _handler_instance
    if _handler_instance is None:
        _handler_instance = TransitHandler()
    return _handler_instance


# Simple function interface for orchestrator
def process_transit_query(user_message: str) -> Dict[str, Any]:
    """
    Simple function to call from orchestrator.
    
    Args:
        user_message: User's message
        
    Returns:
        Dictionary with response data
    """
    handler = get_handler()
    result = handler.handle(user_message)
    return handler.to_dict(result)


# CLI for testing
if __name__ == "__main__":
    import sys
    import json
    
    handler = TransitHandler()
    
    test_queries = [
        "quiero sacar licencia A2",
        "cuanto cuesta el permiso de circulacion",
        "necesito agendar hora para renovar carnet",
        "tengo multas del tag como las pago",
        "que documentos necesito para la licencia",
        "hola como estas",  # Should fail - not transit
    ]
    
    if len(sys.argv) > 1:
        test_queries = [" ".join(sys.argv[1:])]
    
    print("=" * 60)
    print("TRANSIT HANDLER - FULL PIPELINE TEST")
    print("=" * 60)
    
    for query in test_queries:
        print(f"\n{'='*60}")
        print(f"📝 Query: \"{query}\"")
        print("-" * 40)
        
        result = handler.handle(query)
        
        if result.success:
            print(f"✅ Intent: {result.intent_id}")
            print(f"📊 Confidence: {result.confidence:.2%}")
            print(f"📁 Type: {result.intent_type}")
            print(f"\n{result.response_text}")
            
            if result.needs_clarification:
                print("\n⚠️ Low confidence - clarification recommended")
            if result.alternatives:
                print("\n📋 Alternatives:")
                for alt in result.alternatives:
                    print(f"   - {alt['intent_id']}: {alt['confidence']:.2%}")
        else:
            print(f"❌ Not handled: {result.response_text}")
