"""Offline checks for the custom KG-2 extraction and write boundary."""

import json
import unittest

from tests.test_graph import graph, package, CRIMES


class RecordingGraph:
    def __init__(self):
        self.writes = []

    def run(self, cypher, **params):
        self.writes.append((cypher, params))
        return []


class TestCustomExtraction(unittest.TestCase):
    def setUp(self):
        self.doc = package.Document("news-test", "Một người bị bắt; một người khác bị truy tố.", {})

    def test_invalid_json_reports_document(self):
        for response in ("not json", '[]', '{"cases": {}}', '{"cases": [null]}'):
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, "news-test"):
                graph.extract_news_cases(self.doc, lambda prompt: response, CRIMES)

    def test_null_optional_lists_are_empty(self):
        response = json.dumps({"cases": [{"charges": None, "people": None, "substances": None}]})
        cases = graph.extract_news_cases(self.doc, lambda prompt: response, CRIMES)
        self.assertEqual(cases[0]["charges"], [])
        self.assertEqual(cases[0]["people"], [])
        self.assertEqual(cases[0]["substances"], [])

    def test_unrelated_person_charge_stays_unlinked(self):
        response = json.dumps({"cases": [{"charges": [CRIMES[0]], "people": [
            {"name": "Witness", "charge": "lừa đảo chiếm đoạt tài sản"}]}]})
        cases = graph.extract_news_cases(self.doc, lambda prompt: response, CRIMES)
        self.assertEqual(cases[0]["people"][0]["charge"], "")
        recorder = RecordingGraph()
        graph.add_custom_news_case(recorder, cases[0], self.doc, 1)
        self.assertTrue(any("person_id" in params for _, params in recorder.writes))
        self.assertFalse(any("SUBJECT_OF" in cypher for cypher, _ in recorder.writes))

    def test_stage_is_person_specific(self):
        self.assertEqual(graph._stage_for({"stage": "arrested"}, self.doc), "arrested")
        self.assertEqual(graph._stage_for({}, self.doc), "reported")
        self.assertEqual(graph._stage_for({"sentence": "36 tháng tù"}, self.doc), "verdict")

    def test_sentence_combines_years_and_months(self):
        self.assertEqual(graph._sentence_months("8 năm 6 tháng tù"), 102)
        self.assertEqual(graph._sentence_months("36 tháng tù"), 36)
        self.assertIsNone(graph._sentence_months("tử hình"))

    def test_penalty_rank_uses_highest_alternative(self):
        self.assertEqual(graph.parse_penalty("phạt tù 20 năm, tù chung thân hoặc tử hình")[2], 200)
        self.assertEqual(graph.parse_penalty("phạt tù 20 năm hoặc tù chung thân")[2], 100)

    def test_mdma_thresholds_follow_law_units(self):
        docs = graph.load_markdown_docs("data/drug_law")
        doc = next(doc for doc in docs if doc.id == "blhs-dieu-250")
        article = graph.parse_custom_law_article(doc)
        clause = next(clause for clause in article["clauses"] if clause["number"] == 4)
        thresholds = graph.parse_thresholds(clause, doc.id)
        threshold = next(item for item in thresholds if item["substance"] == "MDMA")
        self.assertEqual(threshold["lower_grams"], 100)
        self.assertIsNone(threshold["upper_grams"])
        self.assertEqual(threshold["doc_id"], doc.id)
        self.assertEqual(graph.parse_amount("hơn 9,6kg MDMA")["amount_grams"], 9600)

    def test_only_definitions_article_produces_legal_terms(self):
        recorder = RecordingGraph()
        graph.build_graph(recorder, graph.load_markdown_docs("data/drug_law"), [],
                          lambda *args, **kwargs: self.fail("Law extraction must not call the LLM"))
        terms = [params["term"] for _, params in recorder.writes if "term" in params]
        self.assertEqual(len(terms), 14)
        self.assertIn("Tiền chất", terms)
