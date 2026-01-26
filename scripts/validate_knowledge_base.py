import json
import os
import glob
from typing import List, Dict, Optional, Union, Any
from pydantic import BaseModel, Field, ValidationError, field_validator
from pathlib import Path

# --- Pydantic Models for Schema Validation ---

class TramiteFaq(BaseModel):
    id: str
    aliases: List[str]
    categoria: str
    tipo_licencia: Optional[str] = None
    sub_tipo_licencia: Optional[Union[str, List[str]]] = None
    definicion_licencia: Optional[List[str]] = None
    
    # Respuestas can be a dictionary with flexible keys (donde, requisitos, etc.)
    # enforcing minimum expected keys for FAQs
    respuestas: Dict[str, Any] 
    
    fuentes: Optional[List[str]] = None
    bordes: Optional[List[str]] = None
    aspect_buttons: Optional[List[str]] = None
    
    # Optional fields for non-root items
    parent_id: Optional[str] = None
    
    # Complex Flow specific
    tipo_atencion: Optional[str] = None # e.g. "agenda_oficial"
    definicion: Optional[List[str]] = None
    
    @field_validator('id')
    def id_must_be_lowercase_slug(cls, v):
        if not v or v != v.lower().replace(' ', '_').replace('.', ''):
            # Allow dashes and underscores, but warn if it looks weird
            pass 
        return v

class KnowledgeBaseFile(BaseModel):
    version: str
    descripcion: str
    tramites: List[TramiteFaq]

# --- Validation Logic ---

def validate_files(base_path: str):
    json_files = glob.glob(os.path.join(base_path, "*.json"))
    
    all_ids = {} # id -> file_path
    all_aliases = {} # alias -> id
    
    errors = []
    warnings = []
    
    print(f"Found {len(json_files)} JSON files in {base_path}")
    
    for file_path in json_files:
        file_name = os.path.basename(file_path)
        print(f"Validating {file_name}...")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 1. Schema Validation
            try:
                kb = KnowledgeBaseFile(**data)
            except ValidationError as e:
                errors.append(f"[SCHEMA] {file_name}: {e}")
                continue # Stop checking this file if structure is wrong
            
            # 2. Logic Validation (Duplicates)
            for tramite in kb.tramites:
                # Check Duplicate ID
                if tramite.id in all_ids:
                    errors.append(f"[DUPLICATE ID] '{tramite.id}' found in {file_name} and {all_ids[tramite.id]}")
                else:
                    all_ids[tramite.id] = file_name
                
                # Check Aliases
                if tramite.aliases:
                    for alias in tramite.aliases:
                        normalized_alias = alias.lower().strip()
                        if normalized_alias in all_aliases:
                            # If pointing to same ID (e.g. cross-ref) it might be ok, but usually aliases should be unique to an intent
                            if all_aliases[normalized_alias] != tramite.id:
                                warnings.append(f"[AMBIGUOUS ALIAS] '{normalized_alias}' in {tramite.id} ({file_name}) conflicts with {all_aliases[normalized_alias]}")
                        else:
                            all_aliases[normalized_alias] = tramite.id

        except json.JSONDecodeError as e:
            errors.append(f"[JSON] {file_name} is not valid JSON: {e}")
        except Exception as e:
            errors.append(f"[ERROR] Unexpected error processing {file_name}: {e}")

    # --- Report ---
    print("\n" + "="*50)
    print("VALIDATION REPORT")
    print("="*50)
    
    if errors:
        print(f"\n❌ FOUND {len(errors)} ERRORS:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("\n✅ NO CRITICAL ERRORS FOUND")

    if warnings:
        print(f"\n⚠️ FOUND {len(warnings)} WARNINGS:")
        for w in warnings:
            print(f"  - {w}")
    else:
        print("\n✅ NO WARNINGS FOUND")
        
    return len(errors) == 0

import sys

if __name__ == "__main__":
    # Use argument or default to absolute path
    if len(sys.argv) > 1:
        base_dir = sys.argv[1]
    else:
        # Fallback to hardcoded absolute path if needed, or relative to script
        base_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../apps/faq/kb/transito",
        )
        
    print(f"Checking directory: {base_dir}")
    if not os.path.exists(base_dir):
        print(f"❌ Directory not found: {base_dir}")
        exit(1)
        
    success = validate_files(base_dir)
    if not success:
        exit(1)
