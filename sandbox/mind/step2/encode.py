"""Existing offline E5 query encoder; no model download or corpus builder."""
import hashlib
import os
from pathlib import Path
import sys
import numpy as np

MODEL = "intfloat/multilingual-e5-small"
DIM = 384
TRUNCATE = 2000
PASSAGE_BATCH = 32
CACHE_ENV = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
             "HF_ASSETS_CACHE", "SENTENCE_TRANSFORMERS_HOME")


def _model_directory(model_dir):
    path = Path(model_dir)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("model_directory_invalid")
    return path


def model_identity(*, model_dir, revision):
    """Hash weights in the explicit directory; revision is a caller-pinned declaration."""
    if not isinstance(revision, str) or not revision:
        raise ValueError("revision_invalid")
    weights = _model_directory(model_dir) / "model.safetensors"
    digest = hashlib.sha256()
    with open(weights, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    return {"model": MODEL, "revision": revision, "weights_sha256": digest.hexdigest(),
            "weights_bytes": weights.stat().st_size}


class E5:
    """The fixed encode policy. Loading never touches the network."""

    def __init__(self, *, model_dir, revision):
        path = _model_directory(model_dir)
        if not isinstance(revision, str) or not revision:
            raise ValueError("revision_invalid")
        # The hub caches its paths at import time. Never silently reuse settings
        # from a previously imported hub with an uncontrolled cache root.
        if "huggingface_hub.constants" in sys.modules:
            raise ValueError("encoder_runtime_preloaded")
        # A local-only model directory must not fall back to another user's HF
        # cache. A device file cannot be a cache directory or persist files.
        for name in CACHE_ENV:
            os.environ[name] = os.devnull
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import sentence_transformers
        import torch
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(str(path), device="cpu", local_files_only=True)
        self.policy = {"prefix_passage": "passage: ", "prefix_query": "query: ", "truncate_chars": TRUNCATE,
                       "passage_batch": PASSAGE_BATCH, "query_batch": 1, "normalize": True, "dtype": "float32",
                       "dim": DIM, "device": "cpu", "sentence_transformers": sentence_transformers.__version__,
                       "torch": torch.__version__, "torch_threads": torch.get_num_threads()}

    def query(self, text):
        vector = self.model.encode(["query: " + text[:TRUNCATE]], batch_size=1, normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=False)[0]
        return np.ascontiguousarray(vector, dtype="<f4")
