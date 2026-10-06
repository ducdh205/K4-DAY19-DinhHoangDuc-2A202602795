"""Save/restore the actual custom Neo4j graph around the bonus benchmark.

Snapshots are local artifacts, not fixture extractions or benchmark results.
Restore explicitly replaces the current lab database, then validates counts.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.graph import Neo4jGraph, create_custom_constraints


def identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Invalid snapshot label or relationship type")
    return value


def main():
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["save", "restore"])
    parser.add_argument("--path", default=".kg-backups/custom.json")
    parser.add_argument("--replace", action="store_true", help="Explicitly replace the current lab graph")
    args = parser.parse_args()
    load_dotenv()
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    graph = Neo4jGraph(uri, os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password123"))
    path = Path(args.path)
    try:
        if args.action == "save":
            nodes = graph.run("MATCH (n) RETURN elementId(n) AS ref, labels(n) AS labels, properties(n) AS props")
            if not nodes or not all(row["props"].get("doc_id") for row in nodes):
                raise ValueError("Expected a populated custom graph with source provenance")
            rels = graph.run("MATCH (a)-[r]->(b) RETURN elementId(a) AS start, elementId(b) AS end, "
                             "type(r) AS type, properties(r) AS props")
            constraints = graph.run("SHOW CONSTRAINTS YIELD name RETURN name")
            data = {"format": "actual-custom-kg-v1", "uri": uri, "stats": graph.stats(),
                    "constraints": [row["name"] for row in constraints], "nodes": nodes, "rels": rels}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not args.replace or data.get("format") != "actual-custom-kg-v1" or data.get("uri") != uri:
                raise ValueError("Restore requires --replace and a snapshot of this Neo4j URI")
            if len(data["nodes"]) != data["stats"]["nodes"] or len(data["rels"]) != data["stats"]["relationships"]:
                raise ValueError("Incomplete snapshot; refusing to reset the database")
            groups = defaultdict(list)
            for node in data["nodes"]:
                labels = ":".join(identifier(label) for label in node["labels"])
                groups[labels].append(node)
            rel_groups = defaultdict(list)
            for rel in data["rels"]:
                rel_groups[identifier(rel["type"])].append(rel)
            # reset() removes both data and constraints. Recreate only the custom
            # schema after restoring; HINT's Person.name uniqueness must not stay.
            graph.reset()
            for labels, rows in groups.items():
                graph.run(f"UNWIND $rows AS row CREATE (n:{labels}) SET n=row.props, n._snapshot_ref=row.ref", rows=rows)
            for rel_type, rows in rel_groups.items():
                graph.run(f"UNWIND $rows AS row MATCH (a {{_snapshot_ref:row.start}}), (b {{_snapshot_ref:row.end}}) "
                          f"CREATE (a)-[r:{rel_type}]->(b) SET r=row.props", rows=rows)
            graph.run("MATCH (n) REMOVE n._snapshot_ref")
            create_custom_constraints(graph)
            assert graph.stats() == data["stats"], "Restored counts must equal the actual snapshot"
        print(f"{args.action}: {data['stats']} ({path})")
    finally:
        graph.close()


if __name__ == "__main__":
    main()
