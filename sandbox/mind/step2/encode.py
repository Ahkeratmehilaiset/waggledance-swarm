"""Existing offline E5 query encoder; no model download or corpus builder."""
import hashlib
import os
from pathlib import Path
import numpy as np

MODEL = "intfloat/multilingual-e5-small"
DIM = 384
TRUNCATE = 2000
PASSAGE_BATCH = 32
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub" / "models--intfloat--multilingual-e5-small"

def model_identity():
    """The cached model revision and the sha256 of its weights, so a vector file names exactly what made it."""
    revision = (HF_CACHE / "refs" / "main").read_text(encoding="ascii").strip()
    weights = HF_CACHE / "snapshots" / revision / "model.safetensors"
    digest = hashlib.sha256()
    with open(weights, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    return {"model": MODEL, "revision": revision, "weights_sha256": digest.hexdigest(),
            "weights_bytes": weights.stat().st_size}


class E5:
    """The fixed encode policy. Loading never touches the network."""

    def __init__(self, *, revision):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import sentence_transformers
        import torch
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(MODEL, device="cpu", local_files_only=True, revision=revision)
        self.policy = {"prefix_passage": "passage: ", "prefix_query": "query: ", "truncate_chars": TRUNCATE,
                       "passage_batch": PASSAGE_BATCH, "query_batch": 1, "normalize": True, "dtype": "float32",
                       "dim": DIM, "device": "cpu", "sentence_transformers": sentence_transformers.__version__,
                       "torch": torch.__version__, "torch_threads": torch.get_num_threads()}

    def query(self, text):
        vector = self.model.encode(["query: " + text[:TRUNCATE]], batch_size=1, normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=False)[0]
        return np.ascontiguousarray(vector, dtype="<f4")
