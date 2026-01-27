import os
import statistics
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "mcp-core")))

from interpretativas_engine import InterpretativasEngine


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _load_entries(engine: InterpretativasEngine, dept_id: str) -> Dict[str, Dict]:
    return engine._load_entries(dept_id)  # internal access for calibration


def _collect_queries(entries: Dict[str, Dict]) -> List[Tuple[str, str]]:
    queries: List[Tuple[str, str]] = []
    for entry_id, entry in entries.items():
        aliases = entry.get("aliases") or []
        if not aliases:
            continue
        # Use first 2 aliases if available
        for alias in aliases[:2]:
            queries.append((str(alias), entry_id))
    return queries


def _score_pairs(engine: InterpretativasEngine, dept_id: str, pairs: List[Tuple[str, str]]):
    qa_index = engine._get_qa_index(dept_id)
    scored = []
    for query, expected_id in pairs:
        hits = qa_index.search(query, top_k=5)
        reranked = engine._rerank(query, hits)
        best = reranked[0] if reranked else None
        best_id = best.payload.get("entry_id") if best else None
        best_score = best.score if best else 0.0
        correct = best_id == expected_id
        scored.append(
            {
                "query": query,
                "expected_id": expected_id,
                "best_id": best_id,
                "best_score": best_score,
                "correct": correct,
            }
        )
    return scored


def _summarize_scores(scored: List[Dict]):
    correct_scores = [s["best_score"] for s in scored if s["correct"]]
    wrong_scores = [s["best_score"] for s in scored if not s["correct"]]
    summary = {
        "total": len(scored),
        "correct": len(correct_scores),
        "incorrect": len(wrong_scores),
        "accuracy": (len(correct_scores) / len(scored)) if scored else 0.0,
        "correct_p25": statistics.quantiles(correct_scores, n=4)[0] if len(correct_scores) >= 4 else None,
        "correct_p50": statistics.median(correct_scores) if correct_scores else None,
        "correct_p75": statistics.quantiles(correct_scores, n=4)[2] if len(correct_scores) >= 4 else None,
        "wrong_p50": statistics.median(wrong_scores) if wrong_scores else None,
    }
    return summary


def _recommend_thresholds(summary: Dict) -> Dict[str, float]:
    # Conservative: set QA threshold near p25 of correct scores to keep precision high
    qa_threshold = summary["correct_p25"] or 0.75
    qa_disamb = max(0.55, min(qa_threshold - 0.1, 0.7))
    return {
        "qa_threshold": round(qa_threshold, 3),
        "qa_disambiguate": round(qa_disamb, 3),
    }


def _write_report(report_path: str, dept_id: str, summary: Dict, thresholds: Dict, scored: List[Dict]):
    lines = []
    lines.append("# Reporte de Calibración Interpretativas")
    lines.append("")
    lines.append(f"Departamento: **{dept_id}**")
    lines.append("")
    lines.append("## Resumen")
    lines.append(f"- Total queries: {summary['total']}")
    lines.append(f"- Accuracy (top-1): {summary['accuracy']:.2%}")
    if summary["correct_p25"] is not None:
        lines.append(f"- Score p25 (correctos): {summary['correct_p25']:.3f}")
        lines.append(f"- Score p50 (correctos): {summary['correct_p50']:.3f}")
        lines.append(f"- Score p75 (correctos): {summary['correct_p75']:.3f}")
    if summary["wrong_p50"] is not None:
        lines.append(f"- Score p50 (incorrectos): {summary['wrong_p50']:.3f}")
    lines.append("")
    lines.append("## Recomendación de umbrales")
    lines.append(f"- `INTERP_QA_THRESHOLD`: **{thresholds['qa_threshold']}**")
    lines.append(f"- `INTERP_QA_DISAMBIGUATE`: **{thresholds['qa_disambiguate']}**")
    lines.append("")
    lines.append("## Muestras con baja confianza (top 15)")
    low = sorted(scored, key=lambda x: x["best_score"])[:15]
    for item in low:
        lines.append(
            f"- `{item['query']}` → best `{item['best_id']}` ({item['best_score']:.3f})"
        )
    lines.append("")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    engine = InterpretativasEngine()
    dept_id = engine._get_dept_id("transito") or "transito"
    entries = _load_entries(engine, dept_id)
    pairs = _collect_queries(entries)
    scored = _score_pairs(engine, dept_id, pairs)
    summary = _summarize_scores(scored)
    thresholds = _recommend_thresholds(summary)

    report_path = os.path.join(_repo_root(), "docs", "interpretativas_calibration_report.md")
    _write_report(report_path, dept_id, summary, thresholds, scored)

    print("Reporte generado:", report_path)


if __name__ == "__main__":
    main()
