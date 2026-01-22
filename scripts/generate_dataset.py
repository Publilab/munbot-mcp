import json
import os
import glob
import sys
from typing import List, Dict

def generate_dataset(base_path: str, output_file: str):
    json_files = glob.glob(os.path.join(base_path, "*.json"))
    dataset = []
    
    print(f"Generating dataset from {len(json_files)} files in {base_path}")
    
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
        base_dir = sys.argv[1]
        output_path = sys.argv[2]
    else:
        # Defaults
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../docs/Transito/Json")
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../docs/Transito/dataset_preguntas.json")

    if not os.path.exists(base_dir):
        print(f"❌ Input directory not found: {base_dir}")
        exit(1)
        
    generate_dataset(base_dir, output_path)
