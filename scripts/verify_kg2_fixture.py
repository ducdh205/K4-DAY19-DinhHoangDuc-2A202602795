"""KG-2 smoke test with real Neo4j and fixed article extractions (no API calls).

Run from the repository root: python scripts/verify_kg2_fixture.py
Like bench_kg.py --build, this replaces the current graph with a small corpus.
It verifies database writes, provenance, idempotence and the law/news bridge;
it does not measure LLM extraction quality or benchmark cost.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from src.graph import Neo4jGraph, build_graph, load_markdown_docs


def main():
    load_dotenv()
    law_docs = load_markdown_docs("data/drug_law")
    news_docs = [doc for doc in load_markdown_docs("data/drug_news")
                 if doc.id in {"news-100260918080821054", "news-100260928173914514"}]
    offence = "mua bán trái phép chất ma túy"
    fixtures = {
        "Góp 14 triệu": {"name": "Vụ Lê Minh Thành", "summary": "Lê Minh Thành bị tuyên 36 tháng tù.",
                        "location": "Hà Nội", "charges": [offence],
                        "people": [{"name": "Lê Minh Thành", "charge": offence,
                                    "sentence": "36 tháng tù", "stage": "verdict",
                                    "stage_evidence": "Lê Minh Thành, 31 tuổi, bị tuyên phạt 36 tháng tù"}],
                        "substances": [{"name": "MDMA", "amount": "5 viên"}]},
        "Mua bán hơn 36kg": {"name": "Vụ mua bán hơn 36kg ma túy", "location": "TP.HCM",
                             "charges": [offence], "people": [
                                 {"name": "Trần Thanh Tuấn", "charge": offence,
                                  "sentence": "tử hình", "stage": "verdict"},
                                 {"name": "Trần Minh Tâm", "charge": offence,
                                  "sentence": "tử hình", "stage": "verdict"}]},
    }

    def fixture_llm(prompt, json_mode=False):
        assert json_mode, "Extraction must request JSON"
        for title, case in fixtures.items():
            if f"Tiêu đề: {title}" in prompt:
                return json.dumps({"cases": [case]}, ensure_ascii=False)
        raise AssertionError("Unexpected article in fixture smoke test")

    graph = Neo4jGraph(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                       os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))
    try:
        graph.reset()
        build_graph(graph, law_docs, news_docs, fixture_llm)
        initial_stats = graph.stats()
        build_graph(graph, law_docs, news_docs, fixture_llm)
        assert graph.stats() == initial_stats, "Rebuilding must not duplicate nodes or edges"
        assert not graph.run("MATCH (n) WHERE n.doc_id IS NULL RETURN n LIMIT 1"), "Missing doc_id"
        expected_ids = [doc.id for doc in law_docs + news_docs]
        assert not graph.run("MATCH (n) WHERE NOT n.doc_id IN $ids RETURN n LIMIT 1", ids=expected_ids)
        paths = graph.run(
            "MATCH (a), (b) WHERE a.doc_id STARTS WITH 'blhs-' AND b.doc_id STARTS WITH 'news-' "
            "MATCH p = shortestPath((a)-[*..4]-(b)) "
            "RETURN a.doc_id AS law, b.doc_id AS news, length(p) AS hops LIMIT 5")
        assert paths, "Law/news bridge must have a path within four hops"
        legal_basis = graph.run(
            "MATCH (:LegalArticle {id:'blhs-dieu-251'})-[:ESTABLISHES]->(o:Offence) "
            "MATCH (p:Person {name:'Lê Minh Thành'})-[:SUBJECT_OF]->(ch)-[:FOR_OFFENCE]->(o) "
            "RETURN o.name AS offence")
        assert legal_basis == [{"offence": offence}], "Person must link to the correct law article"
        sentences = graph.run(
            "MATCH (p:Person {name:'Lê Minh Thành'})-[:SUBJECT_OF]->(ch:Charge) "
            "RETURN ch.sentence_months AS months, ch.stage AS stage")
        assert sentences == [{"months": 36, "stage": "verdict"}]
        substances = graph.run("MATCH (s:Substance {id:'mdma'}) RETURN s.source_doc_ids AS sources")
        assert any(news_docs[0].id in row["sources"] for row in substances)
        print(f"FIXTURE ONLY: {len(law_docs)} law documents + {len(news_docs)} news articles; "
              f"{initial_stats['nodes']} nodes / {initial_stats['relationships']} relationships; 0 API calls")
        for row in graph.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label"):
            print(f"node {row['label']}: {row['n']}")
        for row in graph.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY rel"):
            print(f"edge {row['rel']}: {row['n']}")
        print(f"Bridge paths: {json.dumps(paths, ensure_ascii=False)}")
        print("PASS: provenance, bridge, sentence, shared MDMA, idempotent MERGE")
    finally:
        graph.close()


if __name__ == "__main__":
    main()
