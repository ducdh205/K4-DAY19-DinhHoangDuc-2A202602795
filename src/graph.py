"""Knowledge Graph (Neo4j) + GraphRAG over two drug-topic knowledge bases.

Contract (fixed — bench_kg.py and the tests rely on it):
    link_entity(name, known)                       -> one of `known` or None          (TODO KG-1)
    build_graph(graph, law_docs, news_docs, llm_fn)   load both KBs into Neo4j      (TODO KG-2)
        every node created from ONE document carries the property `doc_id`
    Neo4jGraph.context(question, doc_ids)         -> list[str] facts               (TODO KG-3)
    GraphRAGAgent.answer(question, top_k)         -> str                           (TODO KG-4)

Everything else in this file is a HINT: one possible ontology (below). Use it as is, change it,
or design your own — your own ontology + report/ONTOLOGY.md earns the bonus (see SUBMISSION.md).

Suggested ontology (Crime is the bridge between the law KB and the news KB):

    (:Article {id, title, law, doc_id})-[:DEFINES]->(:Crime {name})
    (:Article)-[:HAS_CLAUSE]->(:Clause {id, number, penalty, text})-[:MENTIONS]->(:Substance {name})
    (:Case {name, summary, date, doc_id})-[:CHARGED_WITH]->(:Crime)
    (:Case)-[:INVOLVES {amount}]->(:Substance)
    (:Case)-[:LOCATED_IN]->(:Location {name})
    (:Person {name, aliases})-[:INVOLVED_IN {role, sentence, charge}]->(:Case)
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from .models import Document
from .store import EmbeddingStore

# Canonical substance names: the ones BLHS Chương XX lists, plus common ones in Vietnamese news.
SUBSTANCES = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11", "Ketamine",
              "cần sa", "thuốc phiện", "côca"]
CLAUSE_START = re.compile(r"^(\d+)\.\s", re.MULTILINE)
FOOTNOTE = re.compile(r"\[\d+\]")

def load_markdown_docs(folder: str | Path) -> list[Document]:
    """Read crawler output (.md with a flat `key: "value"` front matter) into Documents."""
    docs = []
    for path in sorted(Path(folder).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        metadata = {k: json.loads(v) for k, v in re.findall(r'^(\w+): (".*")$', front, re.MULTILINE)}
        docs.append(Document(id=metadata.get("doc_id", path.stem), content=body.strip(), metadata=metadata))
    return docs

def normalize_crime(name: str) -> str:
    """'Tội Mua bán trái phép chất ma túy' -> 'mua bán trái phép chất ma túy'."""
    name = re.sub(r"\s+", " ", name.strip().strip("\"'“”").lower())
    return name.removeprefix("tội ").strip()

def link_entity(name: str, known: list[str], normalize: Callable[[str], str] = normalize_crime) -> str | None:
    """Map a free-text mention (e.g. a charge written by a journalist) onto one canonical name in `known`."""
    normalized_name = normalize(name)
    if not normalized_name:
        return None

    # Keep the caller's original spelling as the value; only use normalised
    # values for matching.  `setdefault` also makes duplicates deterministic.
    canonical_by_normalized: dict[str, str] = {}
    for candidate in known:
        normalized_candidate = normalize(candidate)
        if normalized_candidate:
            canonical_by_normalized.setdefault(normalized_candidate, candidate)

    exact = canonical_by_normalized.get(normalized_name)
    if exact is not None:
        return exact

    matches = difflib.get_close_matches(
        normalized_name, list(canonical_by_normalized), n=1, cutoff=0.8
    )
    return canonical_by_normalized[matches[0]] if matches else None

def find_substances(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in SUBSTANCES if name.lower() in lowered]

# ----------------------------------------------------------------------------------------------
# HINT — suggested ontology: extraction helpers
# ----------------------------------------------------------------------------------------------

def parse_law_article(doc: Document) -> dict[str, Any]:
    """Deterministic (regex) extraction for one 'Điều' — law text is regular enough to skip the LLM."""
    article_id = doc.metadata["article"]                       # "Điều 251 BLHS"
    title = doc.metadata["title"].split(". ", 1)[-1]           # "Tội mua bán trái phép chất ma túy"
    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = text.splitlines()[0]
        penalty = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        clauses.append({
            "id": f"{article_id} khoản {start.group(1)}",
            "number": int(start.group(1)),
            "penalty": penalty.group(1).rstrip(".") if penalty else "",
            "text": text,
            "substances": find_substances(text),
        })
    return {
        "id": article_id,
        "law": doc.metadata.get("law", ""),
        "title": title,
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }

NEWS_EXTRACTION_PROMPT = """Bạn trích xuất knowledge graph từ một bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài. Trả về JSON đúng dạng:
{{"cases": [{{
  "name": "tên ngắn của vụ việc, ví dụ: Vụ mua bán 36kg ma túy tại TP.HCM",
  "summary": "1-2 câu tóm tắt",
  "date": "ngày xảy ra/xét xử nếu có, dạng YYYY-MM-DD hoặc chuỗi rỗng",
  "location": "tỉnh/thành phố, chuỗi rỗng nếu không rõ",
  "charges": ["tội danh, BẮT BUỘC chọn đúng nguyên văn từ DANH SÁCH TỘI DANH"],
  "substances": [{{"name": "tên chất, dùng tên chuẩn trong DANH SÁCH CHẤT nếu khớp", "amount": "khối lượng nếu có"}}],
  "people": [{{"name": "họ tên", "aliases": ["biệt danh"], "role": "bị cáo|bị can|nghi phạm|người liên quan|cán bộ",
               "charge": "tội danh của người này (từ DANH SÁCH TỘI DANH) hoặc chuỗi rỗng",
               "sentence": "mức án đã tuyên nếu có, ví dụ: tử hình, 8 năm tù",
               "stage": "reported|arrested|investigated|prosecuted|tried|verdict",
               "stage_evidence": "câu nguyên văn chứng minh giai đoạn của riêng người này"}}]
}}]}}
Bài không nói về vụ việc cụ thể (tuyên truyền, hội nghị...) thì trả về {{"cases": []}}.
Giai đoạn và tội danh phải thuộc đúng người; không gán mọi tội trong vụ cho mọi người.
Không coi phiên tòa dự kiến hoặc kháng cáo đang chờ là bản án mới.
Chỉ gộp thuốc lắc/kẹo vào MDMA khi có giám định hoặc bài xác nhận rõ.

DANH SÁCH TỘI DANH: {crimes}
DANH SÁCH CHẤT: {substances}

Tiêu đề: {title}
Nội dung:
{content}"""

def extract_news_cases(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    """LLM extraction for one news article; charges are re-linked to law-KB crimes in code."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        payload = json.loads(llm_fn(prompt))
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"{doc.id}: LLM response must be JSON with a cases list") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"{doc.id}: LLM response must contain a cases list")
    cases = payload["cases"]
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError(f"{doc.id}: each case must be an object")
        for field, item_type in (("charges", str), ("people", dict), ("substances", dict)):
            values = case.get(field)
            if values is None:
                values = []
            if not isinstance(values, list) or not all(isinstance(value, item_type) for value in values):
                raise ValueError(f"{doc.id}: case.{field} has invalid JSON shape")
            case[field] = values
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        for person in case.get("people", []):
            if not isinstance(person.get("charge") or "", str):
                raise ValueError(f"{doc.id}: person.charge must be a string")
            person["charge"] = link_entity(person.get("charge") or "", known_crimes) or ""
    return cases

# ----------------------------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------------------------

class Neo4jGraph:
    """Thin wrapper over the official neo4j driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params)
        return [record.data() for record in records]

    def reset(self) -> None:
        """Delete every node, relationship and constraint (bench_kg.py calls this before build_graph)."""
        self.run("MATCH (n) DETACH DELETE n")
        for row in self.run("SHOW CONSTRAINTS YIELD name RETURN name"):
            self.run(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")

    def stats(self) -> dict[str, int]:
        nodes = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return {"nodes": nodes, "relationships": rels}

    def seed_facts(self, question: str, doc_ids: list[str], skip_labels: tuple[str, ...] = (),
                   limit: int = 60) -> tuple[list[str], list[str]]:
        """Ontology-independent first step: seed nodes + their 1-hop edges as text facts.

        Seeds = nodes whose `doc_id` is in doc_ids, or whose `name`/`aliases` appear in the question.
        Returns (seed elementIds, facts). Nodes with a label in skip_labels are left out of the facts.
        """
        seeds = self.run(
            """
            MATCH (n)
            WHERE n.doc_id IN $doc_ids
               OR (n.name IS :: STRING AND size(n.name) >= 3 AND toLower($q) CONTAINS toLower(n.name))
               OR any(a IN coalesce(n.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
            RETURN elementId(n) AS id
            """,
            q=question, doc_ids=doc_ids,
        )
        seed_ids = [row["id"] for row in seeds]
        edges = self.run(
            """
            MATCH (s)-[r]-(m)
            WHERE elementId(s) IN $ids
              AND none(l IN labels(s) + labels(m) WHERE l IN $skip)
            WITH DISTINCT r LIMIT $limit
            WITH startNode(r) AS a, r, endNode(r) AS b
            RETURN labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a_name, type(r) AS rel,
                   properties(r) AS props, labels(b)[0] AS b_label, coalesce(b.name, b.id) AS b_name
            """,
            ids=seed_ids, skip=list(skip_labels), limit=limit,
        )
        facts = []
        for e in edges:
            props = ", ".join(f"{k}: {v}" for k, v in e["props"].items() if v)
            facts.append(f"({e['a_label']}: {e['a_name']}) -[{e['rel']}{' {' + props + '}' if props else ''}]-> "
                         f"({e['b_label']}: {e['b_name']})")
        return seed_ids, facts

    # ---------------------------------------------------------------- HINT — suggested ontology: writes

    def suggested_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            """,
            **article,
        )

    def add_news_case(self, case: dict, doc: Document) -> None:
        self.run(
            """
            MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, [])
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence)
            """,
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    # ---------------------------------------------------------------- KG-3

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Graph facts for a question: seeds + 1 hop, then the legal basis of every case reached."""
        # Expand source/person seeds through Charge -> Offence -> LegalArticle.
        # Keep the basic clause, matching mass thresholds, and the highest penalty
        # when explicitly asked; preserve source text rather than inferring a verdict.
        if max_facts <= 0:
            return []
        seed_ids, seed_text = self.seed_facts(
            question, doc_ids, skip_labels=("Clause", "QuantityThreshold"), limit=max_facts
        )
        q = normalize_identifier(question)
        substances = [normalize_identifier(name) for name in find_substances(q)]
        numbers = [int(number) for number in re.findall(r"[đĐ]iều\s+(\d+)", q)]
        aggregate = bool(re.search(r"những\s+vụ|các\s+vụ|vụ\s+(?:việc\s+)?nào", q))
        maximum = any(word in q for word in ("tối đa", "cao nhất"))
        cases = self.run(
            """
            MATCH (c:Case)
            WHERE elementId(c) IN $ids
               OR EXISTS { MATCH (n:NewsArticle)-[:REPORTS]->(c) WHERE elementId(n) IN $ids }
               OR EXISTS {
                   MATCH (c)-[:HAS_CHARGE]->(ch:Charge)
                   WHERE elementId(ch) IN $ids OR EXISTS {
                       MATCH (p:Person)-[:SUBJECT_OF]->(ch) WHERE elementId(p) IN $ids
                   }
               }
               OR ($aggregate AND EXISTS {
                   MATCH (c)-[:INVOLVES]->(s:Substance) WHERE s.id IN $substances
               })
            WITH c
            WHERE NOT EXISTS {
                MATCH (p:Person)
                WHERE toLower($q) CONTAINS toLower(p.name)
                   OR any(alias IN coalesce(p.aliases, []) WHERE toLower($q) CONTAINS toLower(alias))
            } OR EXISTS {
                MATCH (p:Person)-[:SUBJECT_OF]->(:Charge)<-[:HAS_CHARGE]-(c)
                WHERE toLower($q) CONTAINS toLower(p.name)
                   OR any(alias IN coalesce(p.aliases, []) WHERE toLower($q) CONTAINS toLower(alias))
            }
            OPTIONAL MATCH (c)-[:HAS_CHARGE]->(ch:Charge)
            OPTIONAL MATCH (p:Person)-[:SUBJECT_OF]->(ch)
            OPTIONAL MATCH (ch)-[:FOR_OFFENCE]->(o:Offence)
            RETURN DISTINCT c.id AS case_id, c.name AS name, c.summary AS summary, c.doc_id AS doc_id,
                   p.name AS person, o.name AS offence, ch.stage AS stage, ch.sentence_text AS sentence
            ORDER BY case_id, person, offence
            """,
            ids=seed_ids, q=q, aggregate=aggregate, substances=substances,
        )
        case_ids = list(dict.fromkeys(row["case_id"] for row in cases))
        laws = self.run(
            """
            MATCH (a:LegalArticle)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE a.number IN $numbers OR elementId(a) IN $ids OR elementId(cl) IN $ids
               OR EXISTS {
                   MATCH (a)-[:ESTABLISHES]->(:Offence)<-[:FOR_OFFENCE]-(:Charge)<-[:HAS_CHARGE]-(c:Case)
                   WHERE c.id IN $case_ids
               }
            WITH a, cl
            WHERE cl.number = 1
               OR ($maximum AND coalesce(cl.penalty_rank, 0) > 0 AND NOT EXISTS {
                   MATCH (a)-[:HAS_CLAUSE]->(higher:Clause)
                   WHERE coalesce(higher.penalty_rank, 0) > cl.penalty_rank
               })
               OR EXISTS {
                   MATCH (c:Case)-[i:INVOLVES]->(s:Substance)<-[:FOR_SUBSTANCE]-(t:QuantityThreshold)
                         <-[:HAS_THRESHOLD]-(cl)
                   WHERE c.id IN $case_ids AND i.amount_grams >= t.lower_grams
                     AND (t.upper_grams IS NULL OR i.amount_grams < t.upper_grams)
               }
               OR (size($case_ids) = 0 AND EXISTS {
                   MATCH (cl)-[:HAS_THRESHOLD]->(:QuantityThreshold)-[:FOR_SUBSTANCE]->(s:Substance)
                   WHERE s.id IN $substances
               })
            RETURN DISTINCT a.id AS article_id, a.number AS article, a.law AS law, a.title AS title,
                   cl.number AS clause, cl.text AS text, cl.penalty_text AS penalty
            ORDER BY article, clause
            """,
            ids=seed_ids, case_ids=case_ids, numbers=numbers, maximum=maximum, substances=substances,
        )
        definitions = self.run(
            """
            MATCH (a:LegalArticle)-[:HAS_CLAUSE]->(cl:Clause)-[:DEFINES_TERM]->(t:LegalTerm)
            WHERE toLower($q) CONTAINS toLower(t.name)
               OR elementId(t) IN $ids OR elementId(cl) IN $ids
            RETURN DISTINCT a.doc_id AS doc_id, a.number AS article, cl.number AS clause,
                   t.name AS name, t.definition AS definition
            ORDER BY article, clause
            """,
            ids=seed_ids, q=q,
        )
        facts = [f"[{row['doc_id']} - Điều {row['article']}, khoản {row['clause']}] "
                 f"{row['name']} là {row['definition']}" for row in definitions]
        for row in laws:
            text = row["text"]
            if len(text) > 900 and row.get("penalty"):
                # Keep the statutory penalty and the source point for a named
                # substance; unrelated point lists can exhaust a free-tier prompt.
                points = [f"{point}) {segment.strip()}" for point, segment in _point_segments(text)
                          if any(name.lower() in segment.lower() for name in find_substances(question))]
                text = "\n".join([row["penalty"], *points])
            facts.append(f"[{row['article_id']} - Điều {row['article']} {row['law']}: {row['title']}] "
                         f"khoản {row['clause']}: {text}")
        for row in cases:
            facts.append(f"[{row['doc_id']}] Vụ việc {row['name']}: {row['summary'] or ''}")
            if row.get("offence"):
                facts.append(f"[{row['doc_id']}] {row.get('person') or 'Vụ việc'}: "
                             f"tội danh/hành vi {row['offence']}; giai đoạn {row.get('stage') or 'reported'}; "
                             f"mức án theo báo {row.get('sentence') or 'chưa có dữ kiện tuyên án'}.")
        # Legal facts come first so a full seed-edge budget cannot hide the cross-KB answer.
        return list(dict.fromkeys(facts + seed_text))[:max_facts]

# ---------------------------------------------------------------------------------------------- KG-2

SUBSTANCE_ALIASES = {
    "heroin": "Heroine",
    "heroine": "Heroine",
    "cocaine": "Cocaine",
    "methamphetamine": "Methamphetamine",
    "amphetamine": "Amphetamine",
    "mdma": "MDMA",
    "thuốc lắc": "MDMA",
    "kẹo": "MDMA",
    "xlr-11": "XLR-11",
    "ketamine": "Ketamine",
    "cần sa": "cần sa",
    "thuốc phiện": "thuốc phiện",
    "côca": "côca",
}
ARTICLE_NUMBER = re.compile(r"Điều\s+(\d+)")
TERM_DEFINITION = re.compile(r"^\d+\.\s+(.+?)\s+là\s+(.+)$", re.DOTALL)
POINT_START = re.compile(r"^([a-zđ])\)\s", re.MULTILINE)
NUMBER_AND_UNIT = r"(\d+(?:[.,]\d+)?)\s*(gam|g|kilôgam|kg|mililít|ml)"


def normalize_identifier(value: str) -> str:
    """Stable, display-independent identifier for source-scoped entities."""
    value = unicodedata.normalize("NFC", value or "").strip().lower()
    return re.sub(r"\s+", " ", value)


def canonical_substance(name: str) -> str | None:
    """Return a controlled-vocabulary name without guessing unknown substances."""
    normalized = normalize_identifier(name)
    if normalized in SUBSTANCE_ALIASES:
        return SUBSTANCE_ALIASES[normalized]
    return next((substance for substance in SUBSTANCES if normalize_identifier(substance) == normalized), None)


def _to_base_amount(value: str, unit: str) -> tuple[float, str, float | None]:
    """Return numeric value, canonical unit, and grams when this is a mass."""
    number = float(value.replace(",", "."))
    normalized_unit = normalize_identifier(unit)
    if normalized_unit in {"kg", "kilôgam"}:
        return number, "kg", number * 1000
    if normalized_unit in {"g", "gam"}:
        return number, "g", number
    return number, "ml", None


def parse_amount(amount: str) -> dict[str, Any]:
    """Keep the source spelling and add a comparable gram value when possible."""
    match = re.search(NUMBER_AND_UNIT, amount or "", re.IGNORECASE)
    if not match:
        return {"amount_raw": amount or "", "amount_value": None, "unit": "", "amount_grams": None}
    value, unit, grams = _to_base_amount(match.group(1), match.group(2))
    return {"amount_raw": amount, "amount_value": value, "unit": unit, "amount_grams": grams}


def parse_penalty(penalty: str) -> tuple[int | None, int | None, int | None]:
    """Extract comparable years while preserving the statutory prose separately."""
    normalized = normalize_identifier(penalty)
    # Non-numeric alternatives outrank the finite prison term preceding them.
    rank = 200 if "tử hình" in normalized else 100 if "chung thân" in normalized else None
    year_range = re.search(r"từ\s+(\d+)\s+năm\s+đến\s+(\d+)\s+năm", normalized)
    if year_range:
        return int(year_range.group(1)), int(year_range.group(2)), rank or int(year_range.group(2))
    exact_years = re.search(r"tù\s+(\d+)\s+năm", normalized)
    if exact_years:
        value = int(exact_years.group(1))
        return value, value, rank or value
    if "tử hình" in normalized:
        return None, None, 200
    if "chung thân" in normalized:
        return None, None, 100
    return None, None, None


def parse_custom_law_article(doc: Document) -> dict[str, Any]:
    """Deterministically parse the regular law corpus into the custom ontology."""
    article_label = doc.metadata["article"]
    article_match = ARTICLE_NUMBER.search(article_label)
    article_number = int(article_match.group(1)) if article_match else None
    title = doc.metadata["title"].split(". ", 1)[-1]
    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = " ".join(text.splitlines()[0].split())
        penalty_match = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        penalty = penalty_match.group(1).rstrip(".") if penalty_match else ""
        minimum, maximum, rank = parse_penalty(penalty)
        clauses.append({
            "id": f"{doc.id}:khoan:{start.group(1)}",
            "number": int(start.group(1)),
            "text": text,
            "penalty_text": penalty,
            "penalty_min_years": minimum,
            "penalty_max_years": maximum,
            "penalty_rank": rank,
        })
    return {
        "id": doc.id,
        "number": article_number,
        "title": title,
        "law": doc.metadata.get("law", ""),
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }


def _point_segments(text: str) -> list[tuple[str, str]]:
    starts = list(POINT_START.finditer(text))
    if not starts:
        return [("", text)]
    return [
        (match.group(1), text[match.end(): starts[index + 1].start() if index + 1 < len(starts) else len(text)])
        for index, match in enumerate(starts)
    ]


def parse_thresholds(clause: dict[str, Any], doc_id: str) -> list[dict[str, Any]]:
    """Extract simple statutory mass/volume intervals; leave complex equivalence rules as prose."""
    thresholds: list[dict[str, Any]] = []
    for point, segment in _point_segments(clause["text"]):
        substances = find_substances(segment)
        if not substances:
            continue
        ranged = re.search(
            rf"(?:khối lượng|thể tích)\s+từ\s+{NUMBER_AND_UNIT}\s+đến\s+dưới\s+{NUMBER_AND_UNIT}",
            segment,
            re.IGNORECASE,
        )
        open_ended = re.search(
            rf"(?:khối lượng|thể tích)\s+{NUMBER_AND_UNIT}\s+trở\s+lên",
            segment,
            re.IGNORECASE,
        )
        if not ranged and not open_ended:
            continue
        if ranged:
            lower_value, lower_unit, lower_grams = _to_base_amount(ranged.group(1), ranged.group(2))
            upper_value, upper_unit, upper_grams = _to_base_amount(ranged.group(3), ranged.group(4))
        else:
            lower_value, lower_unit, lower_grams = _to_base_amount(open_ended.group(1), open_ended.group(2))
            upper_value, upper_unit, upper_grams = None, "", None
        for substance in substances:
            canonical = canonical_substance(substance)
            if canonical is None:
                continue
            threshold_id = f"{clause['id']}:{normalize_identifier(canonical)}:{lower_value}:{upper_value}:{lower_unit}"
            thresholds.append({
                "id": threshold_id,
                "point": point,
                "substance": canonical,
                "lower_value": lower_value,
                "upper_value": upper_value,
                "unit": lower_unit,
                "lower_grams": lower_grams,
                "upper_grams": upper_grams,
                "lower_inclusive": True,
                "upper_inclusive": False if upper_value is not None else None,
                "doc_id": doc_id,
            })
    return thresholds


def create_custom_constraints(graph: Neo4jGraph) -> None:
    for label in (
        "LegalArticle", "Clause", "LegalTerm", "Offence", "Substance", "QuantityThreshold",
        "NewsArticle", "Case", "Person", "Charge", "Location",
    ):
        graph.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")


def merge_substance(graph: Neo4jGraph, name: str, doc_id: str) -> str | None:
    canonical = canonical_substance(name)
    if canonical is None:
        return None
    substance_id = normalize_identifier(canonical)
    graph.run(
        """
        MERGE (s:Substance {id: $id})
        ON CREATE SET s.name = $canonical_name, s.aliases = [$source_name], s.doc_id = $doc_id, s.source_doc_ids = [$doc_id]
        ON MATCH SET s.aliases = CASE WHEN $source_name IN coalesce(s.aliases, []) THEN s.aliases ELSE s.aliases + $source_name END,
                     s.source_doc_ids = CASE WHEN $doc_id IN coalesce(s.source_doc_ids, []) THEN s.source_doc_ids ELSE s.source_doc_ids + $doc_id END
        """,
        id=substance_id, canonical_name=canonical, source_name=name, doc_id=doc_id,
    )
    return substance_id


def add_custom_law_article(graph: Neo4jGraph, article: dict[str, Any]) -> None:
    graph.run(
        """
        MERGE (a:LegalArticle {id: $id})
        SET a.number = $number, a.title = $title, a.law = $law, a.doc_id = $doc_id
        """,
        **{key: article[key] for key in ("id", "number", "title", "law", "doc_id")},
    )
    if article["crime"]:
        offence_id = normalize_crime(article["crime"])
        graph.run(
            """
            MATCH (a:LegalArticle {id: $article_id})
            MERGE (o:Offence {id: $offence_id})
            ON CREATE SET o.name = $name, o.aliases = [$name], o.doc_id = $doc_id, o.source_doc_ids = [$doc_id]
            ON MATCH SET o.source_doc_ids = CASE WHEN $doc_id IN coalesce(o.source_doc_ids, []) THEN o.source_doc_ids ELSE o.source_doc_ids + $doc_id END
            MERGE (a)-[:ESTABLISHES]->(o)
            """,
            article_id=article["id"], offence_id=offence_id, name=article["crime"], doc_id=article["doc_id"],
        )
    for clause in article["clauses"]:
        graph.run(
            """
            MATCH (a:LegalArticle {id: $article_id})
            MERGE (cl:Clause {id: $id})
            SET cl.number = $number, cl.text = $text, cl.penalty_text = $penalty_text,
                cl.penalty_min_years = $penalty_min_years, cl.penalty_max_years = $penalty_max_years,
                cl.penalty_rank = $penalty_rank, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            """,
            article_id=article["id"], doc_id=article["doc_id"], **clause,
        )
        definition = TERM_DEFINITION.match(" ".join(clause["text"].splitlines()))
        if definition and article["title"] == "Giải thích từ ngữ":
            term = definition.group(1).strip()
            graph.run(
                """
                MATCH (cl:Clause {id: $clause_id})
                MERGE (t:LegalTerm {id: $term_id})
                SET t.name = $term, t.definition = $definition, t.doc_id = $doc_id
                MERGE (cl)-[:DEFINES_TERM]->(t)
                """,
                clause_id=clause["id"], term_id=normalize_identifier(term), term=term,
                definition=definition.group(2).strip(), doc_id=article["doc_id"],
            )
        for threshold in parse_thresholds(clause, article["doc_id"]):
            substance_id = merge_substance(graph, threshold["substance"], article["doc_id"])
            if substance_id is None:
                continue
            graph.run(
                """
                MATCH (cl:Clause {id: $clause_id}), (s:Substance {id: $substance_id})
                MERGE (t:QuantityThreshold {id: $id})
                SET t.lower_value = $lower_value, t.upper_value = $upper_value, t.unit = $unit,
                    t.lower_grams = $lower_grams, t.upper_grams = $upper_grams,
                    t.lower_inclusive = $lower_inclusive, t.upper_inclusive = $upper_inclusive,
                    t.doc_id = $doc_id
                MERGE (cl)-[:HAS_THRESHOLD {point: $point}]->(t)
                MERGE (t)-[:FOR_SUBSTANCE]->(s)
                """,
                clause_id=clause["id"], substance_id=substance_id, **threshold,
            )


def _stage_for(person: dict[str, Any], doc: Document) -> str:
    if person.get("sentence"):
        return "verdict"
    stage = person.get("stage")
    if stage in {"reported", "arrested", "investigated", "prosecuted", "tried", "verdict"}:
        return stage
    return "reported"


def _sentence_months(sentence: str) -> int | None:
    if "chung thân" in (sentence or "").lower() or "tử hình" in (sentence or "").lower():
        return None
    months = re.search(r"(\d+)\s*tháng", sentence or "", re.IGNORECASE)
    years = re.search(r"(\d+)\s*năm", sentence or "", re.IGNORECASE)
    if months or years:
        return (int(years.group(1)) * 12 if years else 0) + (int(months.group(1)) if months else 0)
    return None


def add_custom_news_case(graph: Neo4jGraph, case: dict[str, Any], doc: Document, ordinal: int) -> None:
    case_id = f"{doc.id}:case:{ordinal}"
    graph.run(
        """
        MERGE (n:NewsArticle {id: $news_id})
        SET n.title = $title, n.publisher = $publisher, n.published_at = $published_at, n.doc_id = $news_id
        MERGE (c:Case {id: $case_id})
        SET c.name = $name, c.summary = $summary, c.event_date = $date, c.source_title = $title, c.doc_id = $news_id
        MERGE (n)-[:REPORTS]->(c)
        """,
        news_id=doc.id, case_id=case_id, name=case.get("name") or doc.metadata.get("title", doc.id),
        summary=case.get("summary", ""), date=case.get("date", ""), title=doc.metadata.get("title", ""),
        publisher=doc.metadata.get("publisher", ""), published_at=doc.metadata.get("document_version", ""),
    )
    location = (case.get("location") or "").strip()
    if location:
        graph.run(
            """
            MATCH (c:Case {id: $case_id})
            MERGE (l:Location {id: $location_id})
            ON CREATE SET l.name = $location, l.aliases = [$location], l.doc_id = $doc_id
            MERGE (c)-[:LOCATED_IN {source_text: $location}]->(l)
            """,
            case_id=case_id, location_id=normalize_identifier(location), location=location, doc_id=doc.id,
        )
    for substance in case.get("substances", []):
        raw_name = substance.get("name", "") if isinstance(substance, dict) else ""
        substance_id = merge_substance(graph, raw_name, doc.id)
        if substance_id is None:
            continue
        amount = parse_amount(substance.get("amount", ""))
        graph.run(
            """
            MATCH (c:Case {id: $case_id}), (s:Substance {id: $substance_id})
            MERGE (c)-[r:INVOLVES]->(s)
            SET r.amount_raw = $amount_raw, r.amount_value = $amount_value, r.unit = $unit,
                r.amount_grams = $amount_grams, r.substance_source_name = $source_name, r.evidence = $amount_raw
            """,
            case_id=case_id, substance_id=substance_id, source_name=raw_name, **amount,
        )
    case_charges = [charge for charge in case.get("charges", []) if charge]
    people = [person for person in case.get("people", []) if person.get("name")]
    charge_specs: list[tuple[dict[str, Any] | None, str]] = [(None, charge) for charge in case_charges]
    for person in people:
        person_id = f"{case_id}:person:{normalize_identifier(person['name'])}"
        aliases = person.get("aliases") or []
        aliases = aliases if isinstance(aliases, list) else [aliases]
        graph.run(
            """
            MERGE (p:Person {id: $person_id})
            SET p.name = $name, p.aliases = $aliases, p.role = $role, p.doc_id = $doc_id
            """,
            person_id=person_id, name=person["name"], aliases=aliases, role=person.get("role", ""), doc_id=doc.id,
        )
        if person.get("charge"):
            charge_specs.append((person, person["charge"]))
    for person, offence_name in charge_specs:
        offence_id = normalize_crime(offence_name)
        charge_suffix = normalize_identifier(person["name"]) if person else "case"
        charge_id = f"{case_id}:charge:{charge_suffix}:{offence_id}"
        if person:
            person_id = f"{case_id}:person:{normalize_identifier(person['name'])}"
        stage = _stage_for(person or {}, doc)
        sentence = (person or {}).get("sentence", "")
        graph.run(
            """
            MATCH (c:Case {id: $case_id})
            MERGE (ch:Charge {id: $charge_id})
            SET ch.stage = $stage, ch.stage_evidence = $stage_evidence,
                ch.sentence_text = $sentence, ch.sentence_months = $sentence_months,
                ch.outcome = CASE WHEN $stage = 'verdict' THEN 'reported_verdict' ELSE '' END, ch.doc_id = $doc_id
            MERGE (c)-[:HAS_CHARGE]->(ch)
            MERGE (o:Offence {id: $offence_id})
            ON CREATE SET o.name = $offence_name, o.aliases = [$offence_name], o.doc_id = $doc_id, o.source_doc_ids = [$doc_id]
            ON MATCH SET o.source_doc_ids = CASE WHEN $doc_id IN coalesce(o.source_doc_ids, []) THEN o.source_doc_ids ELSE o.source_doc_ids + $doc_id END
            MERGE (ch)-[:FOR_OFFENCE {match_method: 'link_entity', confidence: 1.0}]->(o)
            """,
            case_id=case_id, charge_id=charge_id, stage=stage, stage_evidence=(person or {}).get("stage_evidence", ""), sentence=sentence,
            sentence_months=_sentence_months(sentence), doc_id=doc.id, offence_id=offence_id, offence_name=offence_name,
        )
        if person:
            graph.run(
                """
                MATCH (p:Person {id: $person_id}), (ch:Charge {id: $charge_id})
                MERGE (p)-[:SUBJECT_OF]->(ch)
                """,
                person_id=person_id, charge_id=charge_id,
            )

def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    """Load both KBs into an empty graph. llm_fn(prompt, json_mode=False) -> str (metered OpenAI chat)."""
    create_custom_constraints(graph)
    articles = [parse_custom_law_article(document) for document in law_docs]
    for article in articles:
        add_custom_law_article(graph, article)

    known_crimes = [article["crime"] for article in articles if article["crime"]]
    for document in news_docs:
        cases = extract_news_cases(document, lambda prompt: llm_fn(prompt, json_mode=True), known_crimes)
        for ordinal, case in enumerate(cases, start=1):
            add_custom_news_case(graph, case, document, ordinal)

# ---------------------------------------------------------------------------------------------- KG-4

GRAPH_PROMPT = """Trả lời câu hỏi chỉ dựa trên ngữ cảnh (đoạn văn bản và dữ kiện từ knowledge graph).
Nêu rõ số Điều luật khi có. Nếu ngữ cảnh không đủ, nói không đủ thông tin.

Dữ kiện knowledge graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""

class GraphRAGAgent:
    """Hybrid GraphRAG: the same vector top-k as flat RAG, plus facts expanded from the graph."""

    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str],
                 graph_fact_char_budget: int = 4800) -> None:
        self.store = store
        self.graph = graph
        self.llm_fn = llm_fn
        self.graph_fact_char_budget = graph_fact_char_budget

    def answer(self, question: str, top_k: int = 3) -> str:
        chunks = self.store.search(question, top_k=top_k)
        doc_ids = list(dict.fromkeys(chunk["metadata"]["doc_id"] for chunk in chunks))
        facts = self.graph.context(question, doc_ids)
        selected, used = [], 0
        for fact in facts:
            if used + len(fact) + 1 <= self.graph_fact_char_budget:
                selected.append(fact)
                used += len(fact) + 1
        if len(selected) < len(facts):
            selected.append("[Graph đã giới hạn theo ngân sách; không suy ra rằng danh sách này là đầy đủ.]")
        context = "\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1))
        prompt = GRAPH_PROMPT.format(facts="\n".join(selected), chunks=context, question=question)
        return self.llm_fn(prompt)
