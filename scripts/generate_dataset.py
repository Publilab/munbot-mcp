import json
import os
import glob
import sys
from typing import List, Dict

def generate_dataset(base_paths, output_file: str):
    if isinstance(base_paths, str):
        base_paths = [base_paths]

    json_files = []
    for base_path in base_paths:
        json_files.extend(glob.glob(os.path.join(base_path, "*.json")))

    dataset = []

    joined_paths = ", ".join(base_paths)
    print(f"Generating dataset from {len(json_files)} files in {joined_paths}")
    
    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            tramites = data.get("tramites", [])
            for tram in tramites:
                tid = tram.get("id")
                aliases = tram.get("aliases", [])
                
                # Determine type
                # Default to FAQ, but check for specific types
                intent_type = "faq"
                if tram.get("tipo_atencion") == "agenda_oficial":
                    intent_type = "agenda"
                
                # Add each alias as a training example
                for alias in aliases:
                    if alias.strip():
                        dataset.append({
                            "utterance": alias.strip(),
                            "label": tid,
                            "type": intent_type,
                            "source_file": os.path.basename(file_path)
                        })
                        
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            
    # Save to output
    print(f"Extracted {len(dataset)} examples.")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Dataset saved to {output_file}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        base_dirs = sys.argv[1:-1]
        output_path = sys.argv[-1]
    elif len(sys.argv) > 1:
        base_dirs = [sys.argv[1]]
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../apps/faq/kb/transito/datasets/dataset_preguntas.json",
        )
    else:
        # Defaults: FAQ + Agenda only
        base_dirs = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "../apps/faq/kb/transito"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "../apps/agenda/kb/transito"),
        ]
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../apps/faq/kb/transito/datasets/dataset_preguntas.json",
        )

    for base_dir in base_dirs:
        if not os.path.exists(base_dir):
            print(f"❌ Input directory not found: {base_dir}")
            exit(1)

    generate_dataset(base_dirs, output_path)
