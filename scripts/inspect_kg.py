"""Read-only inspection of the current graph; no LLM calls or graph reset.

Run from the repository root with its virtualenv: python scripts/inspect_kg.py.
Writes actual query results and KG-3 context to report/KG_INSPECTION.json.
"""

import json
from datetime import datetime, timezone
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from src.graph import GraphRAGAgent, Neo4jGraph
from src import Document, EmbeddingStore


AUDITS = {
    "missing_doc_id": "MATCH (n) WHERE n.doc_id IS NULL OR n.doc_id='' RETURN labels(n) AS labels, n.id AS id",
    "counts": "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC",
    "relationship_counts": "MATCH ()-[r]->() RETURN type(r) AS relationship, count(*) AS n ORDER BY n DESC",
    "missing_bridge": """MATCH (c:Case) WHERE NOT EXISTS {
        MATCH (c)-[:HAS_CHARGE]->(:Charge)-[:FOR_OFFENCE]->(:Offence)<-[:ESTABLISHES]-(:LegalArticle)
        } RETURN c.id AS id, c.name AS name, c.doc_id AS doc_id""",
    "duplicate_people": """MATCH (p:Person) WITH p.name AS name, collect(p.id) AS ids,
        collect(p.doc_id) AS docs WHERE size(ids)>1 RETURN name, ids, docs""",
    "missing_amount": """MATCH (c:Case)-[i:INVOLVES]->(s:Substance)
        WHERE i.amount_grams IS NULL
        RETURN c.name AS name, c.doc_id AS doc_id, s.name AS substance, i.amount_raw AS raw""",
    "missing_sentence_evidence": """MATCH (p:Person)-[:SUBJECT_OF]->(ch:Charge)
        WHERE ch.stage='verdict' AND coalesce(ch.stage_evidence,'')=''
        RETURN p.name AS name, ch.doc_id AS doc_id, ch.sentence_text AS sentence""",
    "missing_penalty": """MATCH (a:LegalArticle)-[:HAS_CLAUSE]->(cl:Clause)
        WHERE a.law='BLHS' AND coalesce(cl.penalty_text,'')=''
        RETURN a.id AS article, cl.number AS clause, cl.text AS text""",
    "mdma_cases": """MATCH (c:Case)-[:INVOLVES]->(:Substance {id:'mdma'})
        RETURN c.id AS id,c.name AS name,c.doc_id AS doc_id ORDER BY doc_id""",
    "mdma_threshold_match": """MATCH (p:Person {name:'Cái Quang Huy'})-[:SUBJECT_OF]->(ch:Charge)
        <-[:HAS_CHARGE]-(c:Case)-[i:INVOLVES]->(s:Substance {id:'mdma'}),
        (ch)-[:FOR_OFFENCE]->(:Offence)<-[:ESTABLISHES]-(a:LegalArticle)
        -[:HAS_CLAUSE]->(cl:Clause)-[:HAS_THRESHOLD]->(t:QuantityThreshold)-[:FOR_SUBSTANCE]->(s)
        WHERE i.amount_grams>=t.lower_grams AND (t.upper_grams IS NULL OR i.amount_grams<t.upper_grams)
        RETURN p.name AS person, a.number AS article, cl.number AS clause,
               i.amount_grams AS grams, t.lower_grams AS lower_grams, cl.penalty_text AS penalty""",
    "verdict_people": """MATCH (p:Person)-[:SUBJECT_OF]->(ch:Charge)
        WHERE ch.stage='verdict' AND ch.sentence_text CONTAINS 'tử hình'
        RETURN p.name AS person, ch.stage AS stage, ch.sentence_text AS sentence, ch.doc_id AS doc_id""",
}


def main():
    load_dotenv()
    graph = Neo4jGraph(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                       os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))
    try:
        questions = json.loads(Path("data/benchmark_kg.json").read_text(encoding="utf-8"))
        result = {"scope": "Current graph only; this is not an LLM benchmark",
                  "inspected_at": datetime.now(timezone.utc).isoformat(), "stats": graph.stats(),
                  "audits": {name: {"cypher": query, "rows": graph.run(query)}
                             for name, query in AUDITS.items()}, "context": {}}
        for item in questions:
            facts = graph.context(item["question"], [])
            result["context"][item["id"]] = {"question": item["question"], "facts": facts}
            print(f"{item['id']}: {len(facts)} context facts")
        facts = graph.context(questions[2]["question"], ["news-100260918080821054"])
        assert any("Điều 251" in fact for fact in facts), "KG-3 must retrieve Article 251"
        store = EmbeddingStore("kg-readonly-inspection")
        store.add_documents([Document("check", "Lê Minh Thành bị tuyên 36 tháng tù.",
                                      {"doc_id": "news-100260918080821054"})])
        prompt = GraphRAGAgent(store, graph, lambda prompt: prompt).answer(questions[2]["question"], top_k=1)
        assert "Điều 251" in prompt and "36 tháng" in prompt, "KG-4 must combine graph and chunks"
        Path("report/KG_INSPECTION.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        for name, audit in result["audits"].items():
            print(f"{name}: {len(audit['rows'])} rows")
        print("PASS: KG-3 and KG-4 on current Neo4j data; wrote report/KG_INSPECTION.json")
    finally:
        graph.close()


if __name__ == "__main__":
    main()
