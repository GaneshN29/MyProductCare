import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")
DATA_DIR = Path(os.getenv("MYPRODUCTCARE_DATA_DIR", ROOT_DIR / "data")).resolve()
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'myproductcare.db').as_posix()}")
QDRANT_PATH = Path(os.getenv("QDRANT_PATH", DATA_DIR / "qdrant")).resolve()
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "product_document_chunks")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "160"))
RETRIEVAL_CANDIDATES = int(os.getenv("RETRIEVAL_CANDIDATES", "15"))
RERANK_RESULTS = int(os.getenv("RERANK_RESULTS", "5"))
MIN_RERANK_SCORE = float(os.getenv("MIN_RERANK_SCORE", "0.15"))
