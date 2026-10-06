# Nhật ký chạy thật

Môi trường: Python 3.12 trong `.venv` của WSL Ubuntu; Neo4j local tại
`localhost:7687`. Không sửa `bench_kg.py` hoặc các test gốc để vượt kiểm tra.

## Test sau khi thêm Groq/local embedding

```text
$ python -m pytest tests/ -q
.................................................................    [100%]
65 passed, 4 subtests passed in 0.20s
```

## Check với Groq GPT-OSS 20B

```text
$ python -u bench_kg.py --check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = groq:openai/gpt-oss-20b | embedding = local:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
[OK] KG-2 build_graph: 386 node / 585 cạnh, đường xuyên 2 KB dài 1 cạnh
[OK] KG-3 context: 24 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00034. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

Chi phí USD là ước tính từ số token API trả về và giá model, không phải hóa
đơn tài khoản. Groq trả reasoning token trong usage của completion, được tính
theo output token. Embedding local không tính tiền API; CPU, điện và dung
lượng tải model không quy đổi USD. Các lượt embedding local vẫn được đếm là
`calls` trong cấu trúc Usage, không phải request HTTP.

Nguồn cho tích hợp provider (đã kiểm tra khi triển khai):

- [Groq: dùng SDK OpenAI và base URL](https://console.groq.com/docs/openai).
- [GPT-OSS 20B: model ID, JSON mode và giá](https://console.groq.com/docs/model/openai/gpt-oss-20b).
- [Groq: reasoning_effort cho GPT-OSS](https://console.groq.com/docs/api-reference#create-chat-completion).
- [FastEmbed: model ONNX đa ngôn ngữ](https://huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q).

Các ảnh `*.fixture.png` là ảnh kiểm thử cũ trên 18 luật + 2 tin với JSON cố
định, không phải graph trích bằng Groq. Không dùng chúng thay cho ảnh nộp bài
`kg_count.png`, `kg_cross_kb.png`, `kg_my_case.png` sau benchmark đầy đủ.

## Lần benchmark đầu bị rate limit

`python -u bench_kg.py --judge` dừng trong `build_graph` với HTTP 429:

```text
Rate limit reached for model `openai/gpt-oss-20b`
tokens per minute (TPM): Limit 8000, Used 5345, Requested 6884.
Please try again in 31.7175s.
```

Chưa sinh file kết quả ở lần này. Đã bổ sung retry theo `retry-after` của Groq,
tối đa 5 lần chờ, mỗi lần không quá 60 giây; lỗi không phải 429 được ném ra
ngay (sau đó bổ sung retry riêng cho lỗi kết nối/timeout). Hai test mới kiểm tra retry và việc không nuốt lỗi khác. Full suite sau
thay đổi: `67 passed, 4 subtests passed in 0.20s`. Benchmark sau đó được chạy
lại từ đầu, không tiếp tục graph đang nạp dở hoặc sửa file số liệu bằng tay.

## Các lần chạy bị gián đoạn và giới hạn prompt

Một lần chạy tiếp theo gặp HTTP 413 ở Q4 Graph: `Limit 8000, Requested 11022`.
Đã giới hạn graph facts ở 4.800 ký tự, ưu tiên luật/định nghĩa trước seed và
giữ nguyên từng fact; khoản dài chỉ giữ hình phạt và điểm về chất được hỏi.
Nếu bỏ fact, prompt cảnh báo danh sách chưa đầy đủ. Chunk top-k của hai
pipeline vẫn giống nhau. Đây là thay đổi retrieval, không sửa benchmark.

Lần sau gặp `APIConnectionError: Server disconnected without sending a response`.
SDK Groq dùng timeout 60 giây; code retry riêng lỗi kết nối, tối đa 5 lần,
thời gian chờ nằm trong latency đo thật.

Ngày 06-10-2026, lần chạy đi đến Q4 Graph rồi dừng ở API chấm điểm với HTTP
413: `Limit 8000, Requested 8628`. Không có file kết quả hoàn chỉnh của lần này.
Giới hạn completion giảm từ 8.192 xuống 4.096 cho JSON (trích xuất/chấm) và
1.536 cho câu trả lời; reasoning effort vẫn `low`. Có log phản hồi opt-in
`LLM_TRACE_PATH` trong thư mục backup bị gitignore, không lưu key/header.
Benchmark được chạy lại toàn bộ bằng `python -u bench_kg.py --judge`.

Kiểm thử sau thay đổi: `73 passed, 4 subtests passed in 0.28s`.

Adapter baseline `src_hint` gọi các hàm HINT nguyên bản; chỉ bổ sung `doc_id`
cho node nguồn còn thiếu, giữ nguyên labels, cạnh và khóa theo tên. Dùng cùng
chat/embedding/extraction prompt/top-k/chunk/budget để hạn chế nhiễu so sánh.
Hai lần build vẫn trích LLM riêng, nên không phải thí nghiệm kiểm soát hoàn
toàn; chỉ có sáu câu và một lượt mỗi ontology.

Trước khi baseline thay graph, snapshot lưu dữ liệu trích xuất thật và tên
constraint; restore dùng `reset()` của lab (xóa cả dữ liệu/ràng buộc), rồi
khôi phục dữ liệu và tạo lại schema custom, kiểm tra tổng node/cạnh. Không dùng fixture để tạo ảnh
nộp bài hoặc file benchmark.

## Blocker quota ngày, 06-10-2026

Lượt `--judge` cuối dừng ở Q4 Graph sau 5 lần retry với lỗi thật:

```text
RateLimitError: Error code: 429
tokens per day (TPD): Limit 200000, Used 197731, Requested 3217.
Please try again in 6m49.536s.
```

Chưa sinh `ket_qua_benchmark_kg.txt`. Các phản hồi Q1–Q3 và Q4 Flat đã trả về
được lưu riêng ở `BENCHMARK_PARTIAL.json`, không thay thế file benchmark.
Graph dựng xong trước khi query: 469 node / 715 cạnh; đã chụp đủ ba ảnh thật.

Lượt `--check` code cuối in ba dòng OK đầu rồi bị chặn khi trích tin ở KG-2:

```text
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
RateLimitError: Error code: 429
tokens per day (TPD): Limit 200000, Used 197035, Requested 3231.
Please try again in 1m54.912s.
```

Đã ngắt retry chờ bằng Ctrl+C, không nhận lượt này là 7 OK. Snapshot restore
khôi phục graph đúng với ảnh (không dùng fixture). Cần quota đủ để chạy lại
benchmark chính/baseline và check; chưa commit/push vì người dùng yêu cầu sau
khi hoàn thành. Test cuối: `73 passed, 4 subtests passed in 0.20s`.

## Tiếp tục với key mới do người dùng thay

Key mới không được hiển thị hay ghi vào log. Một request nhỏ gọi thành công.
Lượt check trên code hiện tại đã đạt đủ 7 OK:

```text
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

Test cùng lượt: `73 passed, 4 subtests passed in 0.38s`.

Phát hiện script snapshot định xóa lại constraint mà `reset()` đã xóa;
test tái hiện thất bại trước sửa, sau sửa full suite đạt
`74 passed, 4 subtests passed in 0.22s`. Không đổi logic benchmark chính.

Benchmark chính hoàn tất thật (8m50s wall-clock cả judge/retry), sinh file
`ket_qua_benchmark_kg.txt`: 469 node / 715 cạnh, 176 chunk; Graph recall 0,71,
judge 1,67; Flat recall 0,23, judge 0,50. Đã đọc toàn bộ Per question và chụp
lại đủ 3 ảnh từ graph của lượt này. Q6 được kiểm toán bằng local embedding,
178 lượt embed, $0 API, không gọi chat; kết quả ở `Q6_CONTEXT_AUDIT.json`.

## Baseline HINT hoàn tất và khôi phục graph chính

```bash
LAB_SOLUTION_PACKAGE=src_hint LLM_TRACE_PATH=.kg-backups/hint-final-trace.jsonl .venv/bin/python -u bench_kg.py --judge --out ket_qua_benchmark_kg.hint.txt
```

Lượt thật hoàn tất trong 8m30s wall-clock (cả judge/retry), 185 node / 349 cạnh:

```text
== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176         0        0   0.00000      9.4
graph       196     39966     3946   0.00418    251.7

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.23   0.33      824      313   0.00016     5.44
graph       0.37   0.83     2382      364   0.00029    16.57
```

Đã đọc đủ Per question. Q4 HINT sai khung tối đa (7 năm), Q5 bịa Điều 249;
Q6 vẫn thiếu. Kiểm toán read-only baseline lưu ở `KG_INSPECTION.hint.json`.
Sau khi baseline xong, chạy `scripts/graph_snapshot.py restore --replace`:
`restore: {'nodes': 469, 'relationships': 715} (.kg-backups/custom.json)`.
Chạy lại inspector read-only: 0 node thiếu doc_id, 0 Case thiếu cầu nối,
2 nhóm tên người trùng, 5 Case MDMA, 1 dòng đối chiếu ngưỡng và 2 người
verdict tử hình. Không gọi chat khi restore/inspect; không đổi benchmark chính.

## Checklist cuối trước commit

```text
$ python -m pytest tests/ -q
.................................................................... [ 91%]
......                                                                   [100%]
74 passed, 4 subtests passed in 0.21s

$ python -m pytest tests/test_base.py tests/test_graph.py -q
................................................                         [100%]
48 passed in 0.12s
```

`python -m compileall -q src src_hint scripts tests` và `git diff --check`
thành công. Các file base/test gốc và benchmark không có diff. Kiểm tra mẫu
API key trong mã/báo cáo/file kết quả và lịch sử Git không thấy key;
`.env`, `.venv/`, `.kg-backups/` bị ignore. Đã xem lại ba ảnh nộp thật:
Q-A bảng 11 labels; Q-B và Q-D có ô query và Results overview.

Khi stage file kết quả mới, `git diff --cached --check` báo trailing spaces
do Markdown xuống dòng trong phản hồi LLM nguyên bản. Giữ nguyên hai file
benchmark theo yêu cầu không chỉnh tay; kiểm tra whitespace staged cho các
file còn lại bằng pathspec loại trừ đúng hai file kết quả đó. Đã bỏ dòng
trống thừa cuối các file test/adapter mới; không đổi hành vi pipeline.
