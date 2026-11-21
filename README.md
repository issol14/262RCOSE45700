# LearnBoost

원문 위에서 묻고 답하는 학습 작업 공간이다. PDF를 올리고 질문하면 문서에서 찾은 근거와 함께 답하고, 답의 인용을 누르면 원문의 그 자리가 형광펜으로 표시된다. 문서에 근거가 없으면 답을 지어내지 않고 보류한다.

2026-2 실전SW프로젝트(COSE457) 8팀.

## 할 수 있는 것

| | 기능 |
|---|---|
| S1 | PDF 업로드 → 출처가 붙은 답 → 기록 |
| S2 | 문장을 골라 질문 → 근거 구간 강조 |
| S3 | 근거가 없으면 보류("문서 내용으로만 답하기"를 끄면 일반 지식으로 답하고 그렇게 표시) |
| S4 | 인용한 자리에 짧은 필기, 헷갈려요 / 이해했어요 표시 |
| S5 | 표 · 그림 영역을 끌어서 질문(비전 모델) |
| S8 | 필기를 넣은 PDF 내려받기 |
| 그 밖에 | 작업 공간, 이어진 대화, 노트, 일지, 할 일, 캔버스, 개념 정리(이해도), 기억, 답변 모델 고르기 |

## 켜기

Python 3.12, [uv](https://docs.astral.sh/uv/), Node 20 이상이 필요하다.

```bash
cp .env.example .env      # LB_LLM_API_KEY, LB_EMBED_URL, LB_RERANK_URL 을 채운다
scripts/run.sh            # http://127.0.0.1:8000  (처음엔 .venv 와 ui/dist 를 만든다)
```

서버는 127.0.0.1에만 열린다. 생성 · 임베딩 · 재정렬은 `.env`에 적은 엔드포인트(Furiosa RNGD)를 쓴다.
올린 파일, 검색 색인, 기록(SQLite)은 `data/` 아래에 쌓이고 git에는 올라가지 않는다.

## 구성

```
ui/        화면 (React, pdf.js)            ui/src, 빌드 결과 ui/dist
           색과 글꼴은 ui/src/tokens.css 한 곳에서 정한다(노트 블루 #2f5bd3, 형광펜 #ffe27a, Pretendard)
server/    API (FastAPI)                   app · documents · chat · concepts · records · prompts
lb/        문서 처리와 답 만들기            parse · index · retrieve · pipeline · generate · region · export · store
bench/     측정                            run · judge · metrics · report
tests/     서버 테스트 (엔드포인트 없이)
```

| 단계 | 구현 | 위치 |
|---|---|---|
| Parse | PyMuPDF 블록, 좌표는 쪽 기준 0–1로 정규화 | `lb/parse.py` |
| Chunk | 한 쪽 안에서 블록을 ~700자로 묶음. 쪽을 넘지 않음 | `lb/parse.py` |
| Index | BGE-M3 dense + BM25(Kiwi 형태소), 문서별 | `lb/index.py` |
| Retrieve | dense 30 + BM25 30 → RRF → bge-reranker-v2-m3로 20개 재정렬 → 상위 5 | `lb/retrieve.py` |
| Gate | 재정렬 최고 점수 < τ(0.01)면 생성 없이 보류 | `lb/pipeline.py` |
| Generate | 질문 유형별 설명 틀 + [n] 인용 | `lb/generate.py` |
| Ground | 인용 [n]의 앞 문장과 겹치는 블록만 강조 좌표로 | `lb/generate.py` |
| Store | SQLite. 작업 공간별 문서, 그리고 기록(대화 · 필기 · 노트 · 할 일 · 캔버스 · 기억 · 개념) | `lb/store.py` |

### API

| | |
|---|---|
| `GET /api/config` · `GET /api/health` | 고를 수 있는 모델, 생성 서버 연결 확인 |
| `GET /api/documents?workspace=` · `POST /api/documents` | 작업 공간의 문서 목록 · 업로드(PDF) |
| `GET /api/documents/{id}/file` · `/text` · `DELETE` · `POST …/index` | 원본 · 텍스트 · 삭제 · 다시 색인 |
| `POST /api/documents/{id}/annotated-file` | 필기를 넣은 PDF |
| `POST /api/chat` | 한 번의 질문(ndjson 스트림: progress · delta · done) |
| `POST /api/concepts/suggest` | 답 뒤의 개념 · 이해도 제안 |
| `GET /api/records` · `POST /api/records` | 기록 불러오기 · 바뀐 것만 저장 |

기록은 서버에 있다. 화면은 처음에 한 번 불러오고, 바뀐 항목만 보낸다. 그래서 다른 브라우저에서 열어도 같은 대화와 필기가 보인다.
문서는 작업 공간마다 따로이고, 작업 공간을 지우면 그 문서와 기록도 함께 지워진다.

## 확인

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q        # 서버 33개, 엔드포인트 없이
cd ui && npm test                    # 화면 모듈 14개
BASE=http://127.0.0.1:8000 PDF=<파일> Q='<문서가 답하는 질문>' node e2e/smoke.mjs   # 브라우저로 끝까지 (앱이 켜져 있을 때)
```

## 측정

Allganize RAG-Evaluation-Dataset-KO로 쟀다. 결과는 `results/REPORT.md`(평가 128문항)와 `results/DEV.md`(개발셋 비교)에 있다.
다시 재려면 문항과 PDF를 `data/allganize`에 두고 `scripts/regress.sh`를 돌린다(생성 약 125회 + 판정 약 120회 호출).

- τ와 설정은 개발셋(텍스트형 29문항)으로만 정했다. 평가셋으로는 고르지 않았다.
- 답은 τ를 끈 채로 만들고 재정렬 점수를 함께 저장한다. 그래서 τ는 나중에 다시 계산할 수 있다.

## 알려진 한계

- 스캔 PDF는 텍스트가 없어 업로드할 때 거절한다. OCR은 범위 밖이다.
- PDF만 받는다. 형광펜과 필기 자리에 쓰는 좌표를 PDF 쪽에서 읽기 때문이다.
- 강조는 텍스트 블록 단위다. 영역 질문의 인용은 고른 영역이 아니라 검색된 문단을 가리킨다.
- 정답 판정은 LLM이 한다. 보류 평가는 다른 분야 문서에 묻는 방식이라 쉽다.
- 한 사람이 쓰는 앱이다. 로그인이 없고, 두 창에서 같은 대화를 동시에 고치면 나중에 저장한 쪽이 남는다.
- 답이 길이 제한에 걸려 잘렸는지는 알리지 못한다.

## 팀원
- 이다솔
- 최도은

## 브랜치 규칙
- `main`: 배포 가능한 상태만 둔다
- `develop`: 통합 브랜치
- `feature/*`: 기능 개발. `develop`으로 PR을 올리고 1명 이상 리뷰 후 머지

## 협업 방식
대표 저장소를 각자 fork해서 작업하고, fork의 `feature/*` 브랜치에서 대표 저장소 `develop`으로 PR을 올린다.
작업 전에 fork를 동기화한다(Sync fork 또는 `git fetch upstream`).
