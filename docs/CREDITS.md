# 누가 무엇을 만들었나

두 사람이 따로 만든 구현을 하나로 합쳤다.

| 부분 | 위치 | 만든 사람 |
|---|---|---|
| 화면: 작업 공간 · 대화 · 문서 뷰어 · 캔버스 · 개념 정리 · 노트 · 일지 · 할 일 · 기억 | `ui/src`, `ui/index.html`, `ui/tests` (`records` 제외) | 최도은 |
| 일반 대화 · 이해도 맞춤 · 개념 제안 프롬프트 | `server/prompts.py` | 최도은 |
| 필기 PDF · 영역 질문 · 답변 모델 고르기 · 요청 검증의 설계 | `lb/export.py`, `lb/region.py`, `lb/config.py`, `server/` | 최도은이 처음 만들고, 이 백엔드에 맞춰 이다솔이 다시 씀 |
| 파싱 · 좌표 · 검색 · 재정렬 · 보류 게이트 · 인용 · 형광펜 좌표 · 필기 글 | `lb/` | 이다솔 |
| API, 저장(SQLite), 화면과 서버의 연결 | `server/`, `lb/store.py`, `ui/src/records.js`, `ui/src/main.jsx`의 연결 부분 | 이다솔 |
| 측정 · 판정 · 결과 | `bench/`, `results/`, `scripts/` | 이다솔 |
| 서버 테스트 · 브라우저 확인 | `tests/`, `ui/e2e` | 이다솔 |
| 색 · 글꼴 토큰 | `ui/src/tokens.css`, 각 화면 CSS의 색 값 | 이다솔 |

## 쓴 것

PyMuPDF, Kiwi(kiwipiepy), FastAPI, React, pdf.js, KaTeX, react-markdown, lucide. 글꼴은 Pretendard(SIL Open Font License 1.1)이고 npm 패키지로 받아 빌드에 함께 넣는다. 글꼴 파일은 레포에 두지 않으며 화면이 외부 주소에서 글꼴을 받아 오지도 않는다. 모델은 Furiosa RNGD 엔드포인트의 BGE-M3, bge-reranker-v2-m3, Gemma 4 · EXAONE 4.5 · GPT OSS · Qwen3, Qwen3-VL.
평가 데이터는 Allganize RAG-Evaluation-Dataset-KO이고 레포에는 넣지 않았다.
