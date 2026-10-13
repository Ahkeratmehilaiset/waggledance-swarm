"""Bake-off v2: lesson chunks keep every block and the lesson ref; tau is chosen on calibration only."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bakeoff_v2 as b  # noqa: E402


def test_chunks_keep_every_block_and_the_lesson_ref(tmp_path):
    path = tmp_path / "feedback_x_y_fable5_20261013.md"
    blocks = ["block %d " % i + "word " * (60 + 40 * i) for i in range(6)] + ["tail " * 400]
    data = ("---\nname: x\ndescription: the key line\n---\n\n" + "\n\n".join(blocks)).encode("utf-8")
    path.write_bytes(data)
    chunks = b.chunk_records(path, data)
    base = f"lesson:{path.stem}@{b.v1.sha(data)[:12]}"
    assert [c["doc_id"] for c in chunks] == [f"{base}#{n}" for n in range(len(chunks))]
    assert {b.base_ref(c["doc_id"]) for c in chunks} == {base}
    key = "feedback x y fable5 20261013. the key line\n"
    bodies = [c["text"][len(key):] for c in chunks]
    assert all(c["text"].startswith(key) and len(body) <= b.CHUNK for c, body in zip(chunks, bodies))
    assert "".join(bodies).replace("\n", "").replace(" ", "") == \
        "".join(blocks).replace("\n", "").replace(" ", "")


def test_tau_maximises_answered_positives_minus_background():
    tau, score = b.choose_tau([0.30, 0.25, 0.05], [0.01, 0.02, 0.04, 0.20])
    assert abs(tau - 0.045) < 1e-9 and score == 0.75  # all 3 positives, 1 of 4 background beats 2/3 and 0
    tau, score = b.choose_tau([0.30, 0.25], [0.01, 0.02, 0.04, 0.20])
    assert abs(tau - 0.225) < 1e-9 and score == 1.0
