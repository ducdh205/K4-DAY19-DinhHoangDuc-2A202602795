"""The bonus adapter must actually call the suggested ontology helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src_hint import graph


class TestHintAdapter(unittest.TestCase):
    def test_build_uses_hint_writers(self):
        fake = SimpleNamespace(suggested_constraints=Mock(), add_law_article=Mock(), add_news_case=Mock(), run=Mock())
        article = {"crime": "mua bán trái phép chất ma túy"}
        doc = object()
        with patch.object(graph, "parse_law_article", return_value=article), \
             patch.object(graph, "extract_news_cases", return_value=[{"name": "case"}]):
            graph.build_graph(fake, [doc], [doc], Mock())
        fake.suggested_constraints.assert_called_once()
        fake.add_law_article.assert_called_once_with(article)
        fake.add_news_case.assert_called_once_with({"name": "case"}, doc)
        self.assertIn("SET n.doc_id", fake.run.call_args.args[0])
