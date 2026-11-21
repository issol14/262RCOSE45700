"""Paths and tunables. Environment variables override defaults."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("LB_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
INDEX_DIR = Path(os.environ.get("LB_INDEX_DIR", DATA / "index"))
STORAGE = Path(os.environ.get("LB_STORAGE", DATA / "app"))

LLM_BASE_URL = os.environ.get("LB_LLM_BASE_URL", "http://127.0.0.1:8001/v1")
LLM_API_KEY = os.environ.get("LB_LLM_API_KEY", "local")
LLM_MODEL = os.environ.get("LB_LLM_MODEL", "cyankiwi/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit")
EMBED_MODEL = os.environ.get("LB_EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.environ.get("LB_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
DEVICE = os.environ.get("LB_DEVICE", "cuda")
# remote embedding / reranking (OpenAI-style /v1/embeddings, Jina-style /v1/rerank); local models when unset
EMBED_URL = os.environ.get("LB_EMBED_URL")
RERANK_URL = os.environ.get("LB_RERANK_URL")
REMOTE_EMBED_MODEL = os.environ.get("LB_REMOTE_EMBED_MODEL", "furiosa-ai/bge-m3")
REMOTE_RERANK_MODEL = os.environ.get("LB_REMOTE_RERANK_MODEL", "furiosa-ai/bge-reranker-v2-m3")

CHUNK_CHARS = int(os.environ.get("LB_CHUNK_CHARS", 700))   # target chunk length (Korean chars)
TOP_DENSE = 30
TOP_BM25 = 30
TOP_RERANK_IN = 20       # candidates passed to the reranker (per document)
MAX_RERANK_POOL = 60     # cap for a space with several documents
TOP_CONTEXT = 5          # passages given to the generator
ABSTAIN_TAU = float(os.environ.get("LB_ABSTAIN_TAU", 0.01))  # min reranker score to answer: just above the highest dev negative (0.005)

MAX_UPLOAD_MB = 50

# Answer models a learner may pick per question.
# The configured default always comes first; the bench never reads this list.
_OFFERED = [
    ("furiosa-ai/gemma-4-31B-it-FP8-block", "Gemma 4 31B", "기본 · 근거 쪽을 가장 잘 고름"),
    ("furiosa-ai/EXAONE-4.5-33B-FP8", "EXAONE 4.5 33B", "한국어 질의와 긴 문서"),
    ("furiosa-ai/gpt-oss-120b", "GPT OSS 120B", "복잡한 풀이 · 느림"),
    ("furiosa-ai/Qwen3-30B-A3B-FP8", "Qwen3 30B A3B", "빠른 답"),
]
CHAT_MODELS = [{"id": i, "label": l, "note": n} for i, l, n in _OFFERED]
if LLM_MODEL not in [m["id"] for m in CHAT_MODELS]:
    CHAT_MODELS.insert(0, {"id": LLM_MODEL, "label": LLM_MODEL.split("/")[-1], "note": "서버에 설정된 모델"})
# S5 region questions (a dragged table / figure area) go to a vision model with the cropped image
VL_MODEL = os.environ.get("LB_VL_MODEL", "furiosa-ai/Qwen3-VL-32B-Instruct")
MAX_QUESTION_CHARS = 4000
MAX_SELECTION_CHARS = 30000
