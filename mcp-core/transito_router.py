"""
Deterministic Router for Transit Department
Uses RapidFuzz for fuzzy matching user queries against FAQ/Agenda intents
"""

import json
import os
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass
from rapidfuzz import fuzz, process

@dataclass
class MatchResult:
    """Result of intent matching"""
    intent_id: str
    confidence: float
    intent_type: str  # 'faq' or 'agenda'
    source_file: str
    matched_utterance: str

class TransitoRouter:
    """
    Deterministic router for Transit department queries.
    Matches user input against pre-defined intents using fuzzy matching.
    """
    
    FAQ_THRESHOLD = 70  # Minimum score to consider a match
    AGENDA_THRESHOLD = 75  # Higher threshold for actions
    AMBIGUITY_MARGIN = 10  # If top 2 results are within this margin, ask for clarification
    
    def __init__(self, dataset_path: str = None):
        """
        Initialize router with dataset of utterances.
        
        Args:
            dataset_path: Path to the augmented dataset JSON file
        """
        if dataset_path is None:
            # Default path relative to mcp-core
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            dataset_path = os.path.join(base_dir, "docs", "Transito", "dataset_preguntas_augmented.json")
        
        self.dataset_path = dataset_path
        self.utterances: List[Dict] = []
        self.utterance_texts: List[str] = []
        self._load_dataset()
    
    def _load_dataset(self):
        """Load the augmented dataset into memory"""
        try:
            with open(self.dataset_path, 'r', encoding='utf-8') as f:
                self.utterances = json.load(f)
            
            # Extract just the utterance texts for faster matching
            self.utterance_texts = [item['utterance'].lower() for item in self.utterances]
            
            print(f"[TransitoRouter] Loaded {len(self.utterances)} utterances from {self.dataset_path}")
        except FileNotFoundError:
            print(f"[TransitoRouter] WARNING: Dataset not found at {self.dataset_path}")
            self.utterances = []
            self.utterance_texts = []
    
    def match(self, user_input: str, top_k: int = 3) -> Optional[MatchResult]:
        """
        Match user input against known intents.
        
        Args:
            user_input: The user's message
            top_k: Number of top matches to consider
            
        Returns:
            MatchResult if confident match found, None otherwise
        """
        if not self.utterance_texts:
            return None
        
        # Normalize input
        normalized_input = user_input.lower().strip()
        
        # Find best matches using token_set_ratio (handles word order differences)
        matches = process.extract(
            normalized_input,
            self.utterance_texts,
            scorer=fuzz.token_set_ratio,
            limit=top_k
        )
        
        if not matches:
            return None
        
        best_match = matches[0]
        best_text, best_score, best_idx = best_match
        
        # Get the full record for the best match
        matched_record = self.utterances[best_idx]
        intent_type = matched_record.get('type', 'faq')
        
        # Determine threshold based on type
        threshold = self.AGENDA_THRESHOLD if intent_type == 'agenda' else self.FAQ_THRESHOLD
        
        # Check if score meets threshold
        if best_score < threshold:
            return None
        
        # Check for ambiguity (multiple close matches)
        if len(matches) > 1:
            second_score = matches[1][1]
            if best_score - second_score <= self.AMBIGUITY_MARGIN:
                # Ambiguous - could log this for later review
                pass
        
        return MatchResult(
            intent_id=matched_record['label'],
            confidence=best_score / 100.0,
            intent_type=intent_type,
            source_file=matched_record.get('source_file', ''),
            matched_utterance=best_text
        )
    
    def get_top_matches(self, user_input: str, top_k: int = 5) -> List[Tuple[str, float, str]]:
        """
        Get top K matches for debugging/disambiguation.
        
        Returns:
            List of (intent_id, confidence, matched_text) tuples
        """
        if not self.utterance_texts:
            return []
        
        normalized_input = user_input.lower().strip()
        
        matches = process.extract(
            normalized_input,
            self.utterance_texts,
            scorer=fuzz.token_set_ratio,
            limit=top_k
        )
        
        results = []
        for text, score, idx in matches:
            record = self.utterances[idx]
            results.append((
                record['label'],
                score / 100.0,
                text
            ))
        
        return results
    
    def is_transit_query(self, user_input: str) -> bool:
        """
        Quick check if this query is related to Transit department.
        Uses keyword matching before full fuzzy search.
        """
        transit_keywords = [
            'licencia', 'carnet', 'permiso', 'circulación', 'circulacion',
            'multa', 'parte', 'tag', 'peaje', 'soap',
            'renovar', 'sacar', 'obtener', 'tramitar',
            'tránsito', 'transito', 'conducir',
            'hora', 'agendar', 'reservar', 'cita'
        ]
        
        normalized = user_input.lower()
        return any(kw in normalized for kw in transit_keywords)


# Singleton instance for easy access
_router_instance: Optional[TransitoRouter] = None

def get_router() -> TransitoRouter:
    """Get or create the singleton router instance"""
    global _router_instance
    if _router_instance is None:
        _router_instance = TransitoRouter()
    return _router_instance


# CLI for testing
if __name__ == "__main__":
    import sys
    
    router = TransitoRouter()
    
    test_queries = [
        "quiero sacar licencia clase A",
        "cuanto cuesta renovar la licencia",
        "necesito agendar una hora",
        "tengo multas del tag",
        "requisitos licencia profesional A2",
        "que es el soap",
        "como pago el permiso de circulacion"
    ]
    
    # If arguments provided, use those instead
    if len(sys.argv) > 1:
        test_queries = [" ".join(sys.argv[1:])]
    
    print("=" * 60)
    print("TRANSITO ROUTER - TEST")
    print("=" * 60)
    
    for query in test_queries:
        print(f"\n📝 Query: \"{query}\"")
        
        result = router.match(query)
        
        if result:
            print(f"   ✅ Intent: {result.intent_id}")
            print(f"   📊 Confidence: {result.confidence:.2%}")
            print(f"   📁 Type: {result.intent_type.upper()}")
            print(f"   🔗 Matched: \"{result.matched_utterance}\"")
        else:
            print("   ❌ No confident match found")
            
            # Show top matches for debugging
            top = router.get_top_matches(query, 3)
            if top:
                print("   📋 Top candidates:")
                for intent_id, conf, text in top:
                    print(f"      - {intent_id}: {conf:.2%} ({text[:40]}...)")
