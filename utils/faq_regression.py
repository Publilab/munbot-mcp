#!/usr/bin/env python3
"""
Recorre docs/reporte FAQS.txt y verifica que cada pregunta dispare la respuesta
correcta según la FAQ configurada en kb/preguntas_frecuentes.json.

Uso:
    python utils/faq_regression.py
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp_core import orchestrator
from mcp_core.utils.kb import match_aspect

REPORT_PATH = REPO_ROOT / "docs" / "reporte FAQS.txt"

SECTION_TITLES = {
    "certificado de residencia definitiva": "cert_residencia_definitiva",
    "licencia de transporte": "licencia transporte espacial",
    "patente comercial": "patente comercial intergalactica",
}

FAQ_KEY_BY_INDEX: Dict[str, Dict[int, str]] = {
    "cert_residencia_definitiva": {
        1: "tiempo_residencia",
        2: "documentos_requeridos",
        3: "certificados_digitales",
        4: "antecedentes_penales",
        5: "plazos_y_seguimiento",
        6: "visa_temporal_vencida",
        7: "causales_rechazo",
    },
    "licencia transporte espacial": {
        1: "clase_licencia",
        2: "requisitos_generales",
        3: "documentos_examen",
        4: "examen_teorico",
        5: "examen_practico",
        6: "reprobado",
    },
    "patente comercial intergalactica": {
        1: "venta_desde_casa",
        2: "requisitos_local",
        3: "verificacion_direccion",
        4: "pago_patente",
        5: "inicio_sin_patente",
        6: "cambio_giro",
    },
}


@dataclass
class QuestionEntry:
    tramite_id: str
    question_number: int
    faq_key: str
    question: str
    expected_answer: str


def _norm_space(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def _detect_section(line: str) -> Optional[str]:
    lowered = line.strip().lower()
    for title in SECTION_TITLES:
        if title in lowered:
            return title
    return None


def _parse_report() -> List[QuestionEntry]:
    if not REPORT_PATH.exists():
        raise FileNotFoundError(f"No se encontró {REPORT_PATH}")

    entries: List[QuestionEntry] = []
    lines = REPORT_PATH.read_text(encoding="utf-8").splitlines()

    current_section: Optional[str] = None
    current_tramite: Optional[str] = None
    current_question_idx: Optional[int] = None
    collecting_questions = False
    collecting_answer = False
    questions_buffer: List[str] = []
    answer_lines: List[str] = []

    def flush_block():
        nonlocal questions_buffer, answer_lines, current_question_idx, current_tramite
        if (
            not current_tramite
            or not current_question_idx
            or not questions_buffer
            or not answer_lines
        ):
            questions_buffer = []
            answer_lines = []
            return
        faq_key = FAQ_KEY_BY_INDEX.get(current_tramite, {}).get(current_question_idx)
        if not faq_key:
            questions_buffer = []
            answer_lines = []
            return
        expected_answer = " ".join(answer_lines).strip()
        for q in questions_buffer:
            entries.append(
                QuestionEntry(
                    tramite_id=current_tramite,
                    question_number=current_question_idx,
                    faq_key=faq_key,
                    question=q,
                    expected_answer=expected_answer,
                )
            )
        questions_buffer = []
        answer_lines = []

    pregunta_re = re.compile(r"^(\d+)\.\s*pregunta", re.IGNORECASE)
    respuesta_re = re.compile(r"^respuesta", re.IGNORECASE)

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        section_title = _detect_section(stripped)
        if section_title:
            flush_block()
            current_section = section_title
            current_tramite = SECTION_TITLES[section_title]
            current_question_idx = None
            collecting_questions = False
            collecting_answer = False
            continue

        match = pregunta_re.match(stripped)
        if match:
            flush_block()
            current_question_idx = int(match.group(1))
            collecting_questions = True
            collecting_answer = False
            continue

        if respuesta_re.match(stripped):
            collecting_questions = False
            collecting_answer = True
            answer_lines = []
            continue

        if collecting_questions and current_tramite:
            cleaned = stripped
            if "->" in cleaned:
                cleaned = cleaned.split("->", 1)[0].strip()
            if cleaned:
                questions_buffer.append(cleaned)
            continue

        if collecting_answer and current_tramite:
            answer_lines.append(stripped)
            continue

    flush_block()
    return entries


def _kb_answer(tramite_id: str, faq_key: str) -> str:
    faq = (orchestrator.KB_FAQ_BY_TID.get(tramite_id) or {}).get("faq") or {}
    return str(faq.get(faq_key, "")).strip()


def resolve_answer(tramite_id: str, question: str) -> Tuple[Optional[str], str]:
    faq_resp = orchestrator.check_faq(tramite_id, question)
    if faq_resp:
        return faq_resp.get("respuesta"), "faq"
    aspecto = match_aspect(question, orchestrator.KB_ASPECT_MAP)
    if aspecto:
        resp_map = orchestrator.KB_BY_ID.get(tramite_id, {}).get("respuestas") or {}
        txt = (resp_map.get(aspecto) or "").strip()
        if txt:
            return txt, f"aspect:{aspecto}"
    return None, "no_match"


def build_report(entries: List[QuestionEntry]) -> Tuple[List[Dict], Dict[str, int]]:
    section_counts = defaultdict(int)
    results: List[Dict] = []
    for entry in entries:
        section_counts[entry.tramite_id] += 1
        expected = _kb_answer(entry.tramite_id, entry.faq_key) or entry.expected_answer
        actual, source = resolve_answer(entry.tramite_id, entry.question)
        ok = _norm_space(expected) == _norm_space(actual)
        results.append(
            {
                "tramite_id": entry.tramite_id,
                "faq_key": entry.faq_key,
                "question": entry.question,
                "expected_answer": expected,
                "actual_answer": actual,
                "source": source,
                "matches": ok,
            }
        )
    return results, section_counts


def main() -> None:
    entries = _parse_report()
    results, section_counts = build_report(entries)
    total = len(results)
    failures = [r for r in results if not r["matches"]]

    print(f"Total preguntas evaluadas: {total}")
    for tid, count in section_counts.items():
        print(f"  - {tid}: {count}")

    print(f"\nPreguntas correctas: {total - len(failures)}")
    print(f"Preguntas con error: {len(failures)}\n")

    if failures:
        print("Detalle de errores:")
        for miss in failures:
            print(f"- [{miss['tramite_id']}::{miss['faq_key']}] {miss['question']}")
            print(f"  Esperado: {miss['expected_answer']}")
            print(f"  Obtenido: {miss.get('actual_answer')}")
            print(f"  Fuente: {miss['source']}\n")


if __name__ == "__main__":
    main()
