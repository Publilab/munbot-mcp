#!/usr/bin/env python3
import os, re, json

# 1) Rutas absolutas
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_DIR   = os.path.join(BASE_DIR, "databases")
MD_PATH  = os.path.join(DB_DIR, "complaint_process.md")
OUT_JSON = os.path.join(DB_DIR, "complaint_chunks.json")

# 2) Leer y descartar front-matter e índice (solo 2 splits)
with open(MD_PATH, encoding="utf-8") as f:
    text = f.read()

# Solo separamos en 3 trozos máximo: front-matter, índice y contenido real
parts = text.split("\n---\n", 2)
if len(parts) >= 3:
    content = parts[2]
else:
    content = text

# 3) Separar en secciones por encabezado nivel 2
sections = re.split(r"(?m)(?=^##\s)", content)[1:]
print(f"→ Detectadas {len(sections)} secciones")

chunks = []
chunk_id = 0
WORD_LIMIT = 200  # palabras máximo por chunk

for sec in sections:
    # 4) Extraer título y cuerpo
    header, body = sec.split("\n", 1)
    title = header.lstrip("# ").strip()

    # 5) Dividir en párrafos (separador: línea en blanco)
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    print(f"  • Sección «{title}»: {len(paras)} párrafos")

    # 6) Agrupar párrafos en chunks de ≤ WORD_LIMIT palabras
    current, wcount = [], 0
    for p in paras:
        words = p.split()
        if current and wcount + len(words) > WORD_LIMIT:
            chunk_id += 1
            chunks.append({
                "id": f"{title.replace(' ', '_')}_{chunk_id}",
                "section": title,
                "text": "\n\n".join(current)
            })
            current, wcount = [], 0
        current.append(p)
        wcount += len(words)

    # Volcar resto de párrafos en un último chunk
    if current:
        chunk_id += 1
        chunks.append({
            "id": f"{title.replace(' ', '_')}_{chunk_id}",
            "section": title,
            "text": "\n\n".join(current)
        })

print(f"→ Generados {len(chunks)} chunks en total.")

# 7) Escribir JSON de salida
with open(OUT_JSON, "w", encoding="utf-8") as out:
    json.dump(chunks, out, ensure_ascii=False, indent=2)

print(f"→ Archivo creado en {OUT_JSON}")
