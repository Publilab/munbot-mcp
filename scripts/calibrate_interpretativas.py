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


def _collect_queries_from_file(path: str) -> List[str]:
    queries: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # remove numeric prefix "12. "
            if line[0].isdigit() and "." in line[:4]:
                line = line.split(".", 1)[1].strip()
            queries.append(line)
    return queries


def _score_pairs(engine: InterpretativasEngine, dept_id: str, queries: List[str]):
    qa_index = engine._get_qa_index(dept_id)
    scored = []
    for query in queries:
        hits = qa_index.search(query, top_k=5)
        reranked = engine._rerank(query, hits)
        best = reranked[0] if reranked else None
        best_id = best.payload.get("entry_id") if best else None
        best_score = best.score if best else 0.0
        scored.append(
            {
                "query": query,
                "best_id": best_id,
                "best_score": best_score,
            }
        )
    return scored


def _summarize_scores(scored: List[Dict]):
    scores = [s["best_score"] for s in scored]
    summary = {
        "total": len(scored),
        "p25": statistics.quantiles(scores, n=4)[0] if len(scores) >= 4 else None,
        "p50": statistics.median(scores) if scores else None,
        "p75": statistics.quantiles(scores, n=4)[2] if len(scores) >= 4 else None,
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
    }
    return summary


def _recommend_thresholds(summary: Dict) -> Dict[str, float]:
    # Heuristic: set threshold near p50, keep disambiguate slightly below
    qa_threshold = summary["p50"] or 0.72
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
    if summary["p25"] is not None:
        lines.append(f"- Score p25: {summary['p25']:.3f}")
        lines.append(f"- Score p50: {summary['p50']:.3f}")
        lines.append(f"- Score p75: {summary['p75']:.3f}")
    if summary["min"] is not None:
        lines.append(f"- Score min: {summary['min']:.3f}")
        lines.append(f"- Score max: {summary['max']:.3f}")
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
    dataset_path = os.path.join(_repo_root(), "docs", "Transito", "preguntas_interpretativas.txt")
    queries = _collect_queries_from_file(dataset_path)
    scored = _score_pairs(engine, dept_id, queries)
    summary = _summarize_scores(scored)
    thresholds = _recommend_thresholds(summary)

    report_path = os.path.join(_repo_root(), "docs", "interpretativas_calibration_report.md")
    _write_report(report_path, dept_id, summary, thresholds, scored)

    print("Reporte generado:", report_path)


if __name__ == "__main__":
    main()
