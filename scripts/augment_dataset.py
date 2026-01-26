#!/usr/bin/env python3
"""
Data Augmentation Script for Chilean Transit FAQ Dataset
Expands each intent from ~5 examples to 30+ with linguistic variations
"""

import json
import os
import random
import re
from typing import List, Dict
import sys

# Chilean Spanish synonyms and variations
CHILEAN_SYNONYMS = {
    "sacar": ["obtener", "conseguir", "tramitar", "gestionar", "pedir"],
    "renovar": ["revalidar", "actualizar", "reacreditar", "extender"],
    "licencia": ["carnet", "permiso de conducir", "pase"],
    "permiso de circulación": ["permiso", "pc", "peaje municipal"],
    "multa": ["parte", "citación", "infracción", "ticket"],
    "pagar": ["cancelar", "abonar", "saldar"],
    "agendar": ["reservar", "solicitar", "pedir", "apartar"],
    "hora": ["cita", "turno", "atención"],
    "cuánto": ["qué valor", "qué precio", "cuánta plata"],
    "dónde": ["en qué lugar", "adónde", "a dónde"],
    "cómo": ["de qué forma", "de qué manera"],
    "requisitos": ["documentos", "papeles", "recaudos", "antecedentes"],
}

# Common typos and variations
TYPO_PATTERNS = [
    ("qu", "k"),  # ke, kiero
    ("ción", "sion"),
    ("cc", "c"),
    ("ll", "y"),  # yo instead of llo
]

# Chilean particles and fillers
CHILEAN_PARTICLES = [
    "oye", "mira", "cachai", "po", "pues", "oiga", "disculpa", 
    "perdona", "una consulta", "tengo una duda"
]

QUESTION_STARTERS = [
    "¿", "me podrías decir ", "quisiera saber ", "necesito saber ",
    "quiero consultar ", "me gustaría saber ", "tengo que "
]

def add_chilean_filler(text: str) -> str:
    """Add Chilean filler words"""
    if random.random() < 0.3:
        filler = random.choice(CHILEAN_PARTICLES)
        if text.startswith("¿"):
            return f"¿{filler} {text[1:]}"
        else:
            return f"{filler} {text}"
    return text

def replace_synonyms(text: str) -> str:
    """Replace words with Chilean synonyms"""
    for original, synonyms in CHILEAN_SYNONYMS.items():
        if original in text.lower():
            if random.random() < 0.4:
                synonym = random.choice(synonyms)
                text = re.sub(rf"\b{original}\b", synonym, text, flags=re.IGNORECASE)
    return text

def add_typos(text: str) -> str:
    """Add common Chilean SMS-style typos"""
    if random.random() < 0.2:
        for original, typo in TYPO_PATTERNS:
            if original in text.lower():
                text = re.sub(rf"{original}", typo, text, flags=re.IGNORECASE, count=1)
    return text

def vary_question_form(text: str) -> str:
    """Convert between question and statement forms"""
    if text.startswith("¿"):
        # Already a question, maybe remove opening ¿
        if random.random() < 0.3:
            return text[1:]
    else:
        # Make it a question
        if random.random() < 0.5:
            starter = random.choice(QUESTION_STARTERS)
            return f"{starter}{text.lower()}"
    return text

def simplify_text(text: str) -> str:
    """Create shortened keyword-style version"""
    # Remove question marks and articles
    text = text.replace("¿", "").replace("?", "")
    text = re.sub(r"\b(el|la|los|las|un|una|de|para|por)\b", "", text, flags=re.IGNORECASE)
    return text.strip()

def augment_utterance(utterance: str, num_variations: int = 5) -> List[str]:
    """Generate variations of a single utterance"""
    variations = [utterance]  # Keep original
    
    for _ in range(num_variations):
        variant = utterance
        
        # Apply random transformations
        if random.random() < 0.5:
            variant = replace_synonyms(variant)
        
        if random.random() < 0.3:
            variant = add_chilean_filler(variant)
        
        if random.random() < 0.4:
            variant = vary_question_form(variant)
        
        if random.random() < 0.2:
            variant = add_typos(variant)
        
        if random.random() < 0.15:
            variant = simplify_text(variant)
        
        # Avoid exact duplicates
        if variant not in variations and variant.strip():
            variations.append(variant.strip())
    
    return variations

def augment_dataset(input_file: str, output_file: str, target_per_label: int = 30):
    """Augment the dataset to reach target examples per label"""
    
    with open(input_file, 'r', encoding='utf-8') as f:
        dataset = json.load(f)
    
    # Group by label
    by_label = {}
    for item in dataset:
        label = item['label']
        if label not in by_label:
            by_label[label] = []
        by_label[label].append(item)
    
    print(f"Original dataset: {len(dataset)} examples across {len(by_label)} labels")
    
    # Augment
    augmented = []
    for label, items in by_label.items():
        current_count = len(items)
        needed = target_per_label - current_count
        
        if needed <= 0:
            # Already have enough
            augmented.extend(items)
            print(f"  {label}: {current_count} (no augmentation needed)")
            continue
        
        # Add originals
        augmented.extend(items)
        
        # Generate new variations
        variations_per_original = max(1, needed // current_count + 1)
        
        for item in items:
            new_variants = augment_utterance(item['utterance'], variations_per_original)
            
            for variant in new_variants[1:]:  # Skip first (original)
                if len([i for i in augmented if i['label'] == label]) >= target_per_label:
                    break
                
                augmented.append({
                    'utterance': variant,
                    'label': label,
                    'type': item['type'],
                    'source_file': item['source_file'],
                    'augmented': True
                })
        
        final_count = len([i for i in augmented if i['label'] == label])
        print(f"  {label}: {current_count} → {final_count} (+{final_count - current_count})")
    
    # Save
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(augmented, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Augmented dataset saved to {output_file}")
    print(f"   Total examples: {len(augmented)} (was {len(dataset)})")

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(
            script_dir,
            "../apps/faq/kb/transito/datasets/dataset_preguntas.json",
        )
    )
    output_path = (
        sys.argv[2]
        if len(sys.argv) > 2
        else os.path.join(
            script_dir,
            "../apps/faq/kb/transito/datasets/dataset_preguntas_augmented.json",
        )
    )
    target = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    
    augment_dataset(input_path, output_path, target)
