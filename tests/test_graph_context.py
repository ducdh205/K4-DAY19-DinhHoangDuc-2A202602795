"""Contract-level context formatting tests; Cypher is verified against real Neo4j."""

import unittest

from tests.test_graph import graph, package


class ContextGraph(graph.Neo4jGraph):
    def __init__(self, cases=(), laws=(), definitions=(), seeds=()):
        self.responses = iter([list(cases), list(laws), list(definitions)])
        self.seed_text = list(seeds)

    def seed_facts(self, question, doc_ids, **kwargs):
        return ["seed-person"], self.seed_text

    def run(self, cypher, **params):
        if "aggregate" in params:
            self.aggregate = params["aggregate"]
        return next(self.responses)


class TestCustomContext(unittest.TestCase):
    def test_multi_hop_law_and_person_fact_are_readable(self):
        fake = ContextGraph(
            cases=[{"case_id": "case-1", "name": "Vụ Thành", "summary": "Mua bán MDMA",
                    "doc_id": "news-1", "person": "Lê Minh Thành", "offence": "mua bán",
                    "stage": "verdict", "sentence": "36 tháng tù"}],
            laws=[{"article_id": "blhs-dieu-251", "article": 251, "law": "BLHS",
                   "title": "Tội mua bán", "clause": 1, "text": "phạt tù từ 02 năm đến 07 năm"}])
        facts = fake.context("Lê Minh Thành bị phạt thế nào?", ["news-1"])
        self.assertTrue(all(isinstance(fact, str) for fact in facts))
        self.assertIn("Điều 251", "\n".join(facts))
        self.assertIn("36 tháng tù", "\n".join(facts))
        self.assertIn("verdict", "\n".join(facts))

    def test_law_is_preserved_when_seed_edges_fill_budget(self):
        fake = ContextGraph(laws=[{"article_id": "blhs-dieu-251", "article": 251,
                                  "law": "BLHS", "title": "Mua bán", "clause": 1, "text": "02–07 năm"}],
                            seeds=["Generic seed edge"] * 60)
        facts = fake.context("Điều 251", [], max_facts=1)
        self.assertEqual(len(facts), 1)
        self.assertIn("Điều 251", facts[0])

    def test_legal_definition_and_empty_budget(self):
        fake = ContextGraph(definitions=[{"doc_id": "pcmt-dieu-2", "article": 2,
                                         "clause": 4, "name": "Tiền chất", "definition": "hóa chất"}])
        self.assertIn("Tiền chất", "\n".join(fake.context("tiền chất là gì?", [])))
        self.assertEqual(ContextGraph().context("test", [], max_facts=0), [])

    def test_long_clause_keeps_penalty_and_requested_substance_point(self):
        fake = ContextGraph(laws=[{"article_id": "blhs-dieu-250", "article": 250,
                                  "law": "BLHS", "title": "Vận chuyển", "clause": 4,
                                  "penalty": "phạt tù 20 năm, tù chung thân hoặc tử hình",
                                  "text": "4. Header\na) " + "irrelevant " * 150 +
                                          "\nb) MDMA có khối lượng 100 gam trở lên;"}])
        facts = fake.context("Khối lượng MDMA thuộc khoản nào?", [])
        self.assertIn("100 gam", facts[0])
        self.assertIn("tử hình", facts[0])
        self.assertLess(len(facts[0]), 500)

    def test_clause_question_is_not_case_aggregation(self):
        fake = ContextGraph()
        fake.context("Với khối lượng MDMA trong vụ này, khoản nào áp dụng?", [])
        self.assertFalse(fake.aggregate)
        other = ContextGraph()
        other.context("Những vụ việc nào liên quan MDMA?", [])
        self.assertTrue(other.aggregate)


class TestHybridRetrieval(unittest.TestCase):
    def test_graph_budget_keeps_whole_priority_facts(self):
        class Store:
            def search(self, question, top_k):
                return [{"content": "chunk", "metadata": {"doc_id": "news-1"}}]
        class Graph:
            def context(self, question, doc_ids):
                return ["Điều 251", "unrelated seed edge " * 1000]
        prompt = graph.GraphRAGAgent(Store(), Graph(), lambda prompt: prompt).answer("test")
        self.assertIn("Điều 251", prompt)
        self.assertNotIn("unrelated seed edge", prompt)
        self.assertIn("chunk", prompt)

    def test_chunk_doc_ids_are_deduplicated(self):
        class Store:
            def search(self, question, top_k):
                self.top_k = top_k
                return [{"content": "chunk one", "metadata": {"doc_id": "news-1"}},
                        {"content": "chunk two", "metadata": {"doc_id": "news-1"}}]

        class Graph:
            def context(self, question, doc_ids):
                self.doc_ids = doc_ids
                return ["Điều 251"]

        store, fake = Store(), Graph()
        prompt = graph.GraphRAGAgent(store, fake, lambda prompt: prompt).answer("test?", top_k=2)
        self.assertEqual(store.top_k, 2)
        self.assertEqual(fake.doc_ids, ["news-1"])
        for text in ("chunk one", "chunk two", "Điều 251", "test?"):
            self.assertIn(text, prompt)
