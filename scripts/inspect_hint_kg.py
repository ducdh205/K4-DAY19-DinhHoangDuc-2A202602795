"""Read-only evidence from the actual bonus baseline graph, without LLM calls."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from src_hint.graph import Neo4jGraph


def main():
    load_dotenv()
    graph = Neo4jGraph(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                       os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))
    try:
        questions = json.loads(Path("data/benchmark_kg.json").read_text(encoding="utf-8"))
        result = {"scope": "Actual suggested graph, read-only; not an LLM benchmark",
                  "inspected_at": datetime.now(timezone.utc).isoformat(), "stats": graph.stats(),
                  "counts": graph.run("MATCH (n) RETURN labels(n)[0] AS label,count(*) AS n ORDER BY n DESC"),
                  "context": {q["id"]: graph.context(q["question"], []) for q in questions}}
        Path("report/KG_INSPECTION.hint.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(result["stats"])
        for question, facts in result["context"].items():
            print(f"{question}: {len(facts)} facts")
    finally:
        graph.close()


if __name__ == "__main__":
    main()
