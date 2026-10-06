"""Reconstruct Q6 retrieval on the actual graph; local embeddings, no chat calls."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from src import EmbeddingStore
from src.graph import GraphRAGAgent, Neo4jGraph
from src.llm import MeteredLLM
from bench_kg import chunk_docs, load_corpus


def main():
    load_dotenv()
    llm = MeteredLLM()
    store = EmbeddingStore("q6-readonly-audit", embedding_fn=llm.embed)
    law, news = load_corpus()
    store.add_documents(chunk_docs(law + news, 800))
    question = json.loads(Path("data/benchmark_kg.json").read_text(encoding="utf-8"))[5]["question"]
    graph = Neo4jGraph(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                       os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))
    try:
        chunks = store.search(question, top_k=3)
        doc_ids = list(dict.fromkeys(chunk["metadata"]["doc_id"] for chunk in chunks))
        facts = graph.context(question, doc_ids)
        prompt = GraphRAGAgent(store, graph, lambda prompt: prompt).answer(question, top_k=3)
        graph_text = prompt.split("Dữ kiện knowledge graph:\n", 1)[1].split("\n\nĐoạn văn bản:", 1)[0]
        result = {"scope": "Read-only reconstruction after the benchmark; same graph/model/chunks/top_k; no chat calls",
                  "question": question, "doc_ids": doc_ids, "all_facts": facts,
                  "selected_graph_text": graph_text, "prompt": prompt,
                  "checks": {name: {"all_facts": any(name in fact for fact in facts),
                                    "selected_graph_text": name in graph_text, "prompt": name in prompt}
                             for name in ("Cái Quang Huy", "Lê Minh Thành", "Pháp y tâm thần")}}
        Path("report/Q6_CONTEXT_AUDIT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["checks"], ensure_ascii=False))
        print(f"Embedding only: {llm.usage.calls} calls, ${llm.usage.usd:.5f}")
    finally:
        graph.close()


if __name__ == "__main__":
    main()
