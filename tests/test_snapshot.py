import unittest
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scripts import graph_snapshot
from scripts.graph_snapshot import identifier


class TestSnapshotIdentifiers(unittest.TestCase):
    def test_valid_labels(self):
        self.assertEqual(identifier("LegalArticle"), "LegalArticle")
        self.assertEqual(identifier("FOR_OFFENCE"), "FOR_OFFENCE")

    def test_query_injection_is_rejected(self):
        with self.assertRaises(ValueError):
            identifier("Person) DETACH DELETE n")

    def test_restore_handles_reset_already_dropping_constraints(self):
        state = {"reset": False}
        def reset():
            state["reset"] = True
        def run(query, **params):
            if query.startswith("SHOW CONSTRAINTS"):
                return [{"name": "constraint_added_by_hint"}]
            if query.startswith("DROP CONSTRAINT") and state["reset"]:
                raise ValueError("Reset already dropped that constraint")
            return []
        graph = SimpleNamespace(reset=reset, run=run, stats=lambda: {"nodes": 1, "relationships": 0}, close=Mock())
        data = {"format": "actual-custom-kg-v1", "uri": "bolt://localhost:7687",
                "stats": graph.stats(), "constraints": [], "rels": [],
                "nodes": [{"ref": "1", "labels": ["Person"], "props": {"id": "p", "doc_id": "news-1"}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(graph_snapshot, "Neo4jGraph", return_value=graph), \
                 patch.object(graph_snapshot, "create_custom_constraints") as constraints, \
                 patch.dict(sys.modules, {"dotenv": SimpleNamespace(load_dotenv=lambda: None)}), \
                 patch.object(sys, "argv", ["snapshot", "restore", "--replace", "--path", str(path)]):
                graph_snapshot.main()
            constraints.assert_called_once_with(graph)
            self.assertTrue(state["reset"])
