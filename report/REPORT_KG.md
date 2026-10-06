# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Đinh Hoàng Đức — **MSSV:** 2A202602795 — **Ngày:** 06-10-2026

Số liệu chính lấy nguyên từ [ket_qua_benchmark_kg.txt](../ket_qua_benchmark_kg.txt),
sinh bằng `python bench_kg.py --judge` trên code hiện tại. Đã đọc đủ Per question.
Ontology và đường đi Q1–Q6 ở [ONTOLOGY.md](ONTOLOGY.md).

Cấu hình: Groq `openai/gpt-oss-20b`, reasoning `low`; embedding CPU local
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` qua FastEmbed ONNX,
384 chiều, input tối đa 128 token. Hai pipeline cùng top-k=3, chunk_size=800,
176 chunk từ 18 điều luật + 20 bài báo. Graph thật: **469 node / 715 cạnh**,
11 labels/11 relationship types; không dùng fixture cho benchmark hoặc ảnh nộp.

## 1. Chi phí

Hai bảng dưới copy nguyên văn từ file kết quả:

```text
== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176         0        0   0.00000     12.4
graph       196     39966     4040   0.00421    276.4

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.23   0.50      824      315   0.00016     8.15
graph       0.71   1.67     2346      197   0.00024    16.06
```

Tỷ lệ tính trên số đã làm tròn trong bảng, không phải hóa đơn tài khoản:

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | ---: | ---: | ---: |
| Indexing USD | 0,00000 | 0,00421 | Không xác định (chia cho 0) |
| Indexing giây | 12,4 | 276,4 | 22,29 lần |
| Mỗi câu: USD | 0,00016 | 0,00024 | 1,50 lần |
| Mỗi câu: giây | 8,15 | 16,06 | 1,97 lần |
| Mỗi câu: in_tok | 824 | 2346 | 2,85 lần |
| Mỗi câu: out_tok | 315 | 197 | 0,63 lần |

Local embedding không tính USD API nhưng vẫn tốn CPU, điện, dung lượng tải và
thời gian. 176 `calls` indexing Flat là 176 lượt embedding local; Graph cộng
20 request trích xuất tin. Không dùng mock embedding. Model được cache trước
lượt cuối, nên 12,4 giây không bao gồm tải model ban đầu.
[Model ONNX đã dùng](https://huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q).

Graph tăng phí ở trích xuất tin khi indexing và facts thêm vào prompt khi query.
Input tăng 2,85 lần nhưng output giảm: Q5 Flat lặp nội dung bịa và bị giới hạn
completion, kéo trung bình output Flat lên. Vì thế không thể suy phí query chỉ
từ tỷ lệ input. Groq giới hạn token/phút gây retry; thời gian chờ nằm trong
latency đo thật, không phải toàn bộ chênh lệch là thời gian truy vấn Neo4j.

Giá chat dùng để ước tính: $0.075 input / $0.30 output mỗi triệu token, theo
[Groq pricing](https://console.groq.com/docs/model/openai/gpt-oss-20b).
Reasoning được tính trong output usage; chưa áp dụng giảm giá cached input.
Chi phí/thời gian judge nằm ngoài bảng pipeline theo benchmark gốc.
Các lượt thất bại trước lượt cuối cũng có thể tốn API, nhưng không được cộng
vào một lượt benchmark hoàn chỉnh; xem [RUN_LOG.md](RUN_LOG.md).

Ước tính từ bảng đã làm tròn cho sáu câu: Flat $0,00096, Graph
$0,00421 + 6 × $0,00024 = **$0,00565**, khoảng **5,89 lần**.
Không có điểm hòa vốn *tiết kiệm API* với cấu hình này: Graph vừa có phí
indexing dương vừa có phí query cao hơn. KG chỉ đáng trả thêm nếu giá trị của
câu trả lời đúng/lý do truy vết bù được khoản tăng thêm.

## 2. Từng câu Q1–Q6

Judge thang 0–2; bên thắng xét cả nội dung thật, không chỉ substring recall.

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao |
| --- | --- | --- | --- | --- | --- |
| Q1 | single-hop-law | 0,00 / 0 | 1,00 / 2 | Graph | LegalTerm cung cấp đúng định nghĩa tiền chất, Flat nói không đủ thông tin. |
| Q2 | single-hop-news | 1,00 / 2 | 1,00 / 2 | Hòa chất lượng; Flat rẻ/nhanh hơn | Cả hai nêu đúng Trần Thanh Tuấn và Trần Minh Tâm; Flat 10,30s, Graph 19,63s. |
| Q3 | cross-kb | 0,00 / 0 | 0,00 / 2 | Graph | Nối được án 36 tháng → tội mua bán → Điều 251 → khung cơ bản 2–7 năm; recall literal sai vì format. |
| Q4 | cross-kb | 0,00 / 0 | 1,00 / 2 | Graph | Trả tổ chức sử dụng, Điều 255 khoản 4, 20 năm hoặc chung thân; Flat thiếu thông tin. |
| Q5 | cross-kb-multi-hop | 0,40 / 1 | 0,60 / 2 | Graph | Đối chiếu 9600g MDMA với ngưỡng 100g, đúng khoản 4 Điều 250 và khung 20 năm/chung thân/tử hình. |
| Q6 | aggregation | 0,00 / 0 | 0,67 / 0 | Cả hai thất bại | Graph chỉ liệt kê Huy/Đạt, bỏ Thành và phủ nhận vụ Pháp y; recall bị tăng bởi tên trong câu phủ định. |

Q5 Flat nêu đúng hành vi/chất nhưng bịa “Điều 10” và “Luật ố 15/2019/QH14”,
rồi lặp dài. Judge 1 phản ánh đúng *một phần*, không xác nhận phần luật đó.
Q5 Graph đúng theo corpus nhưng dùng khoảng trắng U+202F trong “Điều 250”,
“Khoản 4”, làm recall chỉ 0,60. Không sửa file kết quả hoặc `must_include`.

Trên riêng Q3–Q5: Graph judge **2,00/2**, Flat **0,33/2**; recall từ điểm
hiển thị là Graph 0,53, Flat 0,13. Q6 chứng minh có graph không tự động đảm bảo
câu trả lời liệt kê đầy đủ.

## 3. Phân tích lỗi

### E3 — Một người ngoài đời có hai node Person

**Hiện tượng:** Dương Minh Tuấn (Hoàng Nato) thành hai node ở hai bài cùng
chuyên án. Cả hai nguồn nêu 43 tuổi, bí danh Hoàng Nato, phường Gia Định,
bị bắt về tổ chức sử dụng trái phép chất ma túy; đây không chỉ là tên trùng.

**Bằng chứng:** thực thi trên graph đầy đủ; kết quả trong
[KG_INSPECTION.json](KG_INSPECTION.json), audit `duplicate_people`:

```cypher
MATCH (p:Person)
WITH p.name AS name, collect(p.id) AS ids, collect(p.doc_id) AS docs
WHERE size(ids)>1
RETURN name, ids, docs;
```

Dòng Dương Minh Tuấn:

```text
news-100260920221957595:case:1:person:dương minh tuấn
news-100260922111804786:case:1:person:dương minh tuấn
```

Đối chiếu hai bài nguồn
[21-9](../data/drug_news/news-100260920221957595.md) và
[22-9](../data/drug_news/news-100260922111804786.md).
Truy vấn còn trả Nguyễn Tiến Đạt ở hai case cùng bài; hai sự kiện khác nhau
không tự chứng minh chúng là hai người khác nhau.

**Nguyên nhân:** ontology/KG-2 dùng ID theo case để tránh gộp nhầm người chỉ vì
cùng tên. UNIQUE `Person.id` đảm bảo idempotency theo nguồn, không giải quyết
nhận diện liên-bài. Đây là đánh đổi còn lỗi, không phải chứng minh ID mới
đã giảm mọi dạng trùng.

**Đề xuất sửa:** tách PersonMention theo tài liệu và Person canonical; đề xuất
SAME_AS từ tên + tuổi + alias + địa điểm + sự kiện rồi xác minh trước hợp nhất.
Sửa phần tạo người của `src/graph.py`, giữ nguồn từng mention. Đánh đổi:
thêm entity-resolution và nguy cơ gộp sai nếu chỉ dựa tên.

### E4 — Recall literal đánh sai cả câu đúng và câu thiếu

**Hiện tượng:** Q3 Graph recall=0,00 nhưng judge=2; Q6 Graph recall=0,67
nhưng judge=0. Điểm substring không biểu diễn độ đúng ngữ nghĩa.

**Bằng chứng Q3:** trích Per question, giữ khoảng trắng đặc biệt giữa các từ;
chỉ bỏ spaces cuối dòng trong phần hiển thị (bản gốc giữ nguyên trong file):

```text
**Lê Minh Thành**
- **Mức án:** 36 tháng tù.
- **Tội danh:** *mua bán trái phép chất ma túy*.
- **Điều luật quy định:** **Điều 251 Bộ luật Hình sự**.
- **Khung hình phạt cơ bản:** 2 năm – 7 năm tù.
```

Ba `must_include` là “36 tháng”, “Điều 251”, “02 năm đến 07 năm”.
API dùng U+202F, Markdown và viết “2 năm – 7 năm”, nên cả ba không khớp
substring. Nội dung lại đúng số tháng, tội, điều, khung.

**Bằng chứng Q6:** câu trả lời trích nguyên văn có đoạn:

> **Lưu ý**: Các vụ việc khác trong đoạn văn bản (ví dụ: vụ “tiệc” ma túy ở Sầm Sơn, các hành vi sai phạm tại Viện Pháp y tâm thần Trung ương, v.v.) không được ghi nhận trong dữ kiện knowledge graph và do đó không được xác định là liên quan đến MDMA. Nếu cần thông tin chi tiết hơn về mức án hoặc các tình tiết khác, cần có dữ kiện bổ sung.

Chuỗi “Pháp y tâm thần” nằm trong lời phủ định, vẫn được keyword_recall tính
là một ý có mặt. Cùng “Cái Quang Huy”, nó cho 2/3 keyword dù câu thiếu/sai.

**Nguyên nhân:** phép đo ở `bench_kg.py` chỉ lower-case + substring, không
chuẩn hóa Unicode/format, đơn vị/số hay phân biệt khẳng định/phủ định. Lỗi
Q3 không phải cầu nối gãy: context đã có Điều 251.

**Đề xuất sửa:** thêm phép đo bổ sung chuẩn hóa Unicode/whitespace/Markdown,
trích trường tháng/điều/khung và kiểm tra khẳng định; báo song song literal
recall và semantic judge. Không sửa benchmark/test gốc để nâng điểm.
Judge dùng cùng GPT-OSS 20B và chỉ một lần nên cũng không độc lập, không tuyệt
đối; cần kiểm tra thủ công phần pháp luật.

### E5 — Câu trả lời Q6 lệch với graph thật

**Hiện tượng:** GraphRAG liệt kê Huy/Đạt, bỏ Lê Minh Thành và nói vụ Pháp y
không được ghi nhận trong KG, trong khi Neo4j có cả hai.

**Bằng chứng:** câu trả lời Q6 Graph trong file benchmark và truy vấn thực tế:

```cypher
MATCH (c:Case)-[:INVOLVES]->(:Substance {id:'mdma'})
RETURN c.id AS id,c.name AS name,c.doc_id AS doc_id ORDER BY doc_id;
```

Kết quả năm dòng (audit `mdma_cases`):

| ID Case | Tên trong graph |
| --- | --- |
| `news-100260917203001265:case:2` | Vụ vận chuyển ma túy của Nguyễn Tiến Đạt |
| `news-100260917203001265:case:1` | Vụ vận chuyển ma túy của Cái Quang Huy |
| `news-100260918080821054:case:1` | Vụ mua bán ma túy tại Hà Nội |
| `news-100260920221957595:case:1` | Vụ bắt giang hồ 'Hoàng Nato' và 126 người liên quan 8 đường dây ma túy |
| `news-100260930085028036:case:1` | Vụ trốn viện và sử dụng ma túy tại Viện Pháp y tâm thần Trung ương |

Năm Case không nhất thiết là năm vụ độc lập: Huy/Đạt có hai Case trong cùng
bài; Hoàng Nato được map “thuốc lắc” bằng heuristic chưa có xác nhận thành phần.
Nhưng Thành và vụ Pháp y là dữ kiện thật bị bỏ sót, đủ chứng minh E5.

[Q6_CONTEXT_AUDIT.json](Q6_CONTEXT_AUDIT.json) tái dựng retrieval **sau**
benchmark trên cùng graph/model/chunk/top-k, không gọi chat: facts đầy đủ có
cả Huy/Thành/Pháp y; facts đã chọn vẫn có Thành nhưng không còn Pháp y; toàn
prompt vẫn nhắc Pháp y qua chunk. Đây là tái dựng chẩn đoán, không thay thế log
nguyên prompt tại thời điểm API gọi.

**Nguyên nhân:** KG-3 ưu tiên luật ngay cả câu liệt kê vụ; KG-4 ngân sách 4800
ký tự làm mất facts cuối về Pháp y. LLM còn bỏ qua Thành dù facts được chọn
có tên/vụ/mức án và suy luận “không được ghi nhận” dù prompt cảnh báo danh sách
chưa đầy đủ. Lỗi nằm ở retrieval budget **và** tổng hợp trả lời.

**Đề xuất sửa:** với aggregation, ưu tiên bộ `Case + source + Substance +
amount` trước luật; trả bảng một hàng mỗi vụ/bài, khử trùng theo chứng cứ.
Dành ngân sách riêng cho danh sách vụ, kiểm tra các Case ID đầu ra so với
Cypher, cấm suy “không tồn tại” từ context cắt bớt. Đánh đổi: prompt dài hơn
hoặc thêm lượt tóm tắt/kiểm tra; phải benchmark lại, không coi đề xuất là đã sửa.

## 4. Kết luận

Với corpus này, KG đáng phí thêm khi cần nối án tin tức với luật hoặc đối
chiếu khối lượng: Q3–Q5 judge Graph 2,00 so với Flat 0,33; trung bình sáu câu
là 1,67 so với 0,50. Giá tăng thêm khoảng $0,00421 indexing và $0,00008/câu
theo bảng làm tròn; latency trung bình tăng từ 8,15 lên 16,06 giây. Corpus luật
được tái sử dụng nhiều và yêu cầu truy vết nguồn là điều kiện thuận lợi cho KG.

Flat đủ cho câu tin một-hop như Q2: cùng judge 2, lại nhanh hơn 9,33 giây.
KG hiện **chưa đạt** yêu cầu liệt kê đầy đủ Q6; phải sửa chính sách ngữ cảnh/
xác minh đầu ra trước khi dùng cho aggregation. Không thể tuyên bố KG luôn
thắng hoặc tiết kiệm API. Sáu câu, một lượt và judge cùng model chưa đủ để
khái quát thống kê.

Đây là benchmark kỹ thuật trên văn bản nguồn, không phải tư vấn pháp lý.

## 5. Tự kiểm và ảnh

```text
$ python -m pytest tests/ -q
.................................................................... [ 91%]
......                                                                   [100%]
74 passed, 4 subtests passed in 0.21s

$ python -m pytest tests/test_base.py tests/test_graph.py -q
................................................                         [100%]
48 passed in 0.12s

$ python -u bench_kg.py --check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = groq:openai/gpt-oss-20b | embedding = local:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
[OK] KG-2 build_graph: 386 node / 585 cạnh, đường xuyên 2 KB dài 1 cạnh
[OK] KG-3 context: 24 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00036. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

48 test gốc và `bench_kg.py` không bị sửa; không có raise NotImplementedError.
Graph full có 0 node thiếu doc_id, 0 Case thiếu cầu nối sang luật.
Có 7 NewsArticle/8 Case: 13/20 bài được LLM trả case rỗng, không giả định tất
cả bài đều có vụ cụ thể. Chưa kiểm toán đủ 13 bài này: danh sách rỗng vẫn có
thể là bỏ sót trích xuất, không chứng minh bài không có vụ. “0 Case thiếu cầu
nối” chỉ xét Case đã tạo, không đo coverage toàn corpus. Kết quả truy vấn lưu ở KG_INSPECTION.json, không phải
một lượt LLM benchmark khác.

Ba ảnh Neo4j Browser thật từ graph của lượt chính, đã `:clear` trước từng query:

- [Đếm node](img/kg_count.png): đủ 11 labels, cộng 469 node.
- [Cầu nối hai KB](img/kg_cross_kb.png): kết quả 24 node / 26 cạnh, có Results overview.
- [Trần Thanh Tuấn](img/kg_my_case.png): 6 node / 5 cạnh, có Results overview; khác Lê Minh Thành.

Không dùng ảnh mẫu hay ảnh fixture để nộp. Snapshot cục bộ giữ graph thật để
khôi phục sau baseline; đã khôi phục và kiểm toán lại đúng 469/715, 0 node
thiếu doc_id và 0 Case thiếu cầu nối (baseline không thay file kết quả chính).
`.env`, `.venv/`, snapshot và trace API riêng bị gitignore; key không nằm
trong báo cáo hay mã nguồn.

## 6. So sánh bonus

Đã chạy thật ontology gợi ý qua `LAB_SOLUTION_PACKAGE=src_hint`, sinh
[ket_qua_benchmark_kg.hint.txt](../ket_qua_benchmark_kg.hint.txt), đủ 12 câu trả
lời và judge. Adapter gọi các hàm HINT, giữ topology/khóa tên; chỉ bổ sung
doc_id thiếu. Graph HINT: 185 node / 349 cạnh. Không dùng replay/fixture.

| GraphRAG | HINT | Custom | Custom / HINT |
| --- | ---: | ---: | ---: |
| Recall trung bình | 0,37 | 0,71 | 1,92 |
| Judge trung bình | 0,83 | 1,67 | 2,01 |
| Indexing USD | 0,00418 | 0,00421 | 1,01 |
| Query USD/câu | 0,00029 | 0,00024 | 0,83 |
| Query giây/câu | 16,57 | 16,06 | 0,97 |

Q1 judge tăng 0 → 2 nhờ node định nghĩa; Q4 tăng 0 → 2 nhờ truy xuất khung
cao nhất thay khoản 1; Q5 tăng 1 → 2 nhờ ngưỡng số và context đúng điều/khoản.
Q2/Q3 hòa judge 2; Q6 cả hai judge 0. Q4 HINT nói nguyên văn:

> Vì vậy, **phạt tù tối đa** mà “Hoàng Nato” có thể bị áp dụng là **7 năm**.

Context HINT trong `KG_INSPECTION.hint.json` chỉ lấy khoản 1 Điều 255; graph
vẫn có khoản 4. Đây là ví dụ E2: không phải thiếu toàn điều luật, mà chính sách
lấy khoản thiếu ngữ cảnh cần thiết. Custom lấy khoản 4 với 20 năm hoặc chung
thân. Q5 HINT bịa Điều 249 và lặp đến giới hạn completion, làm output dài và
phí query cao hơn; không suy rằng nhiều node hơn tự nhiên tiết kiệm token.

Mục 7 [ONTOLOGY.md](ONTOLOGY.md) có bảng cả sáu câu và bằng chứng ngưỡng:
9600g MDMA ≥ 100g, Điều 250 khoản 4. Hai lượt cùng cấu hình nhưng LLM trích
xuất/chấm riêng; Flat judge cũng đổi 0,33 → 0,50. Một lượt/sáu câu chưa chứng
minh ý nghĩa thống kê, và so sánh gồm cả ontology lẫn retrieval tương ứng.
Không nhận giảm trùng liên-bài hay sửa đầy đủ Q6 là cải thiện đã đạt.

## Vấn đề gặp phải

Đã gặp thiếu key, HTTP 429 TPM/TPD, HTTP 413 prompt/completion quá lớn và lỗi
kết nối; các lượt thất bại được ghi riêng trong RUN_LOG.md/BENCHMARK_PARTIAL.json.
Sau khi người dùng thay key, check và benchmark chính đã hoàn tất thật.
Giới hạn facts/completion là đánh đổi đã áp dụng vào code cuối, ảnh hưởng rõ
đến Q6; file benchmark không được chỉnh tay. Hiện không còn blocker cho lượt
chính. Chưa thao tác nộp trên trang khóa học vì không có URL/trang nộp cụ thể.
