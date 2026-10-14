"""HEX MEMORY STEP 2, encode: the corpus vectors, computed ONCE and retained, so a cell is rebuilt without re-encoding.

usage: python -B sandbox/mind/step2/encode.py --corpus <corpus dir> --out <vectors dir>

The encoder is WaggleDance's declared ``intfloat/multilingual-e5-small`` (384 dims), loaded from the local Hugging
Face cache only: ``HF_HUB_OFFLINE``/``TRANSFORMERS_OFFLINE`` are forced on and ``local_files_only`` is passed, so
nothing is downloaded. The policy is fixed and recorded: ``passage: `` + text[:2000] in corpus order (record id
order) in batches of 32, ``query: `` + text[:2000] one query per batch, L2-normalized float32. e5 on CPU is not
bit-reproducible across different batch compositions (2026-10-13 bake-off: 36 of 40 re-encoded cells differed by up
to 7.45e-8), which is why rebuilds use the retained vectors and never re-encode.

Two views per record, encoded in two fixed passes: ``w`` = the record ``text`` and ``d`` = its ``summary`` (a lesson's
own description line). Writes only under ``--out``: ``vectors.f32`` (row-major, little-endian float32, 2 x count rows:
every ``w`` vector in record order, then every ``d`` vector in record order) and ``vectors-manifest.json`` (record
ids sha256, corpus sha256, vectors sha256, encoder identity). No network, no bridge event, no gate code.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus as corpus_mod  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-vectors.v2"
MODEL = "intfloat/multilingual-e5-small"
DIM = 384
VIEWS = ("w", "d")
VIEW_FIELD = {"w": "text", "d": "summary"}
TRUNCATE = 2000
PASSAGE_BATCH = 32
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub" / "models--intfloat--multilingual-e5-small"


class EncodeError(ValueError):
    """An encoder or vector file that cannot be used as given."""


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

    def __init__(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import sentence_transformers
        import torch
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(MODEL, device="cpu", local_files_only=True)
        self.policy = {"prefix_passage": "passage: ", "prefix_query": "query: ", "truncate_chars": TRUNCATE,
                       "passage_batch": PASSAGE_BATCH, "query_batch": 1, "normalize": True, "dtype": "float32",
                       "dim": DIM, "device": "cpu", "sentence_transformers": sentence_transformers.__version__,
                       "torch": torch.__version__, "torch_threads": torch.get_num_threads()}

    def passages(self, texts):
        vectors = self.model.encode(["passage: " + text[:TRUNCATE] for text in texts], batch_size=PASSAGE_BATCH,
                                    normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return np.ascontiguousarray(vectors, dtype="<f4")

    def query(self, text):
        vector = self.model.encode(["query: " + text[:TRUNCATE]], batch_size=1, normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=False)[0]
        return np.ascontiguousarray(vector, dtype="<f4")


def build(corpus_dir, out_dir):
    corpus_manifest, records = corpus_mod.read_corpus(corpus_dir)
    started = time.perf_counter()
    encoder = E5()
    vectors = np.vstack([encoder.passages([record[VIEW_FIELD[view]] for record in records]) for view in VIEWS])
    encode_s = round(time.perf_counter() - started, 1)
    if vectors.shape != (len(VIEWS) * len(records), DIM) or not np.isfinite(vectors).all():
        raise EncodeError("vector_shape_or_value")
    out_dir.mkdir(parents=True, exist_ok=True)
    body = vectors.tobytes()
    (out_dir / "vectors.f32").write_bytes(body)
    manifest = {
        "schema": SCHEMA, "authority": "none", "scope": "shadow_memory_not_gate", "views": list(VIEWS),
        "view_fields": VIEW_FIELD, "count": len(records), "rows": len(VIEWS) * len(records), "dim": DIM,
        "corpus_sha256": corpus_manifest["corpus_sha256"],
        "record_ids_sha256": corpus_manifest["record_ids_sha256"], "vectors_sha256": hashlib.sha256(body).hexdigest(),
        "encoder": dict(model_identity(), **encoder.policy), "encode_seconds": encode_s,
    }
    (out_dir / "vectors-manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest


def read_vectors(vectors_dir, corpus_manifest):
    """{view: count x dim array} after checking the file against its manifest and the corpus it was made from."""
    manifest = json.loads((vectors_dir / "vectors-manifest.json").read_text(encoding="utf-8"))
    body = (vectors_dir / "vectors.f32").read_bytes()
    if hashlib.sha256(body).hexdigest() != manifest["vectors_sha256"]:
        raise EncodeError("vectors_sha256_mismatch")
    if manifest["corpus_sha256"] != corpus_manifest["corpus_sha256"] or \
            manifest["record_ids_sha256"] != corpus_manifest["record_ids_sha256"]:
        raise EncodeError("vectors_not_from_this_corpus")
    if tuple(manifest["views"]) != VIEWS or len(body) != manifest["rows"] * manifest["dim"] * 4 \
            or manifest["rows"] != len(VIEWS) * manifest["count"]:
        raise EncodeError("vectors_size")
    rows = np.frombuffer(body, dtype="<f4").reshape(manifest["rows"], manifest["dim"])
    return manifest, {view: rows[i * manifest["count"]:(i + 1) * manifest["count"]] for i, view in enumerate(VIEWS)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build(args.corpus, args.out)
    print(json.dumps({key: manifest[key] for key in ("count", "dim", "vectors_sha256", "encode_seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
