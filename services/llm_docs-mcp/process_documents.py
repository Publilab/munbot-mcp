import os
import re
import shutil
import nltk
nltk.download('punkt')
from nltk.tokenize import sent_tokenize

def clean_text(text):
    # Eliminar múltiples espacios en blanco y saltos de línea innecesarios
    text = re.sub(r'\s+', ' ', text)
    # Eliminar caracteres no imprimibles
    text = re.sub(r'[^\x20-\x7E]+', '', text)
    # Eliminar encabezados y pies de página comunes
    text = re.sub(r'Page \d+', '', text)
    # Eliminar líneas vacías
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()

def split_text_into_chunks(text, max_chunk_size=2048):
    sentences = sent_tokenize(text)
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        # Si agregar la oración supera el límite, inicia un nuevo chunk
        if len(current_chunk) + len(sentence) + 1 > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            current_chunk += " " + sentence
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

def process_files(input_dir, output_dir):
    # Limpiar la carpeta de fragmentos antes de procesar
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    for filename in os.listdir(input_dir):
        if filename.endswith('.txt'):
            input_path = os.path.join(input_dir, filename)
            with open(input_path, 'r', encoding='utf-8') as file:
                raw_text = file.read()
                cleaned_text = clean_text(raw_text)
                chunks = split_text_into_chunks(cleaned_text)
            
            # Guardar cada fragmento en la carpeta clean
            for i, chunk in enumerate(chunks):
                chunk_filename = f"{os.path.splitext(filename)[0]}_part{i+1}.txt"
                chunk_path = os.path.join(output_dir, chunk_filename)
                with open(chunk_path, 'w', encoding='utf-8') as chunk_file:
                    chunk_file.write(chunk)
            
            print(f"Procesado {filename} y guardado {len(chunks)} fragmentos en {output_dir}/")

if __name__ == "__main__":
    base_dir = os.path.dirname(__file__)
    input_dir = os.path.join(base_dir, 'documents')
    output_dir = os.path.join(base_dir, 'documents', 'clean')
    process_files(input_dir, output_dir)