"""Reproducible suggested ontology, selected via LAB_SOLUTION_PACKAGE=src_hint.

The benchmark itself is not edited. KG-1, extraction prompt, base RAG, chat,
embedding, top-k and prompt budget are shared with src for a fair comparison.
Only graph writes and multi-hop rules use the codelab's suggested ontology.
"""

import re

from src.graph import (Neo4jGraph as BaseGraph, GraphRAGAgent, extract_news_cases,
                       find_substances, link_entity, load_markdown_docs, parse_law_article)


def build_graph(graph, law_docs, news_docs, llm_fn):
    graph.suggested_constraints()
    articles = [parse_law_article(doc) for doc in law_docs]
    for article in articles:
        graph.add_law_article(article)
    crimes = [article["crime"] for article in articles if article["crime"]]
    for doc in news_docs:
        for case in extract_news_cases(doc, lambda prompt: llm_fn(prompt, json_mode=True), crimes):
            graph.add_news_case(case, doc)
    # Preserve the mandatory provenance contract without changing HINT topology
    # or its name-based keys. Shared nodes use their first sorted adjacent source.
    graph.run("""MATCH (n) WHERE n.doc_id IS NULL
                 MATCH (n)--(source) WHERE source.doc_id IS NOT NULL
                 WITH n, min(source.doc_id) AS doc_id SET n.doc_id=doc_id""")


class Neo4jGraph(BaseGraph):
    def context(self, question, doc_ids, max_facts=60):
        if max_facts <= 0:
            return []
        seed_ids, seed_text = self.seed_facts(question, doc_ids, skip_labels=("Clause",), limit=max_facts)
        cases = self.run(
            """MATCH (k:Case)
               WHERE elementId(k) IN $ids OR EXISTS {
                   MATCH (s)--(k) WHERE elementId(s) IN $ids
               }
               RETURN DISTINCT k.name AS name, k.summary AS summary, k.doc_id AS doc_id""",
            ids=seed_ids,
        )
        laws = self.run(
            """MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
               WHERE elementId(a) IN $ids
                  OR any(number IN $numbers WHERE a.id STARTS WITH 'Điều ' + number + ' ')
                  OR EXISTS {
                      MATCH (a)-[:DEFINES]->(:Crime)<-[:CHARGED_WITH]-(k:Case)
                      WHERE k.name IN $cases
                  }
               WITH a, cl
               WHERE cl.number=1 OR EXISTS {
                   MATCH (cl)-[:MENTIONS]->(s:Substance)
                   WHERE s.name IN $substances OR EXISTS {
                       MATCH (k:Case)-[:INVOLVES]->(s) WHERE k.name IN $cases
                   }
               }
               RETURN DISTINCT a.id AS article, a.doc_id AS doc_id,
                      cl.number AS clause, cl.text AS text
               ORDER BY article, clause""",
            ids=seed_ids, numbers=re.findall(r"[đĐ]iều\s+(\d+)", question),
            cases=[row["name"] for row in cases], substances=find_substances(question),
        )
        facts = [f"[{row['doc_id']} - {row['article']}] khoản {row['clause']}: {row['text']}" for row in laws]
        facts.extend(f"[{row['doc_id']}] Vụ việc {row['name']}: {row['summary']}" for row in cases)
        return list(dict.fromkeys(facts + seed_text))[:max_facts]
