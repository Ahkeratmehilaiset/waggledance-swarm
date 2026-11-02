"""Synthetic cell-to-projection binding; no native index, filesystem or model."""
import copy
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint


def _sample():
    projection = {"records": [
        {"record_id": "r1", "stored_text": "public synthetic one", "time_utc": "2026-01-01T00:00:01Z",
         "provenance": {"available_utc": "2026-01-01T00:00:02Z", "event_ref": "a" * 64}},
        {"record_id": "r2", "stored_text": "public synthetic two", "time_utc": "2026-01-01T00:00:03Z",
         "provenance": {"available_utc": "2026-01-01T00:00:04Z", "event_ref": "b" * 64}},
    ]}
    rows, texts = [], []
    for record in projection["records"]:
        for view in ("w", "d"):
            rows.append({"time_utc": record["time_utc"],
                         "provenance": {"record": record["record_id"], "view": view,
                                        **record["provenance"]}})
            texts.append(record["stored_text"])
    cell = SimpleNamespace(rows=rows, collection=SimpleNamespace(_texts=texts))
    return projection, SimpleNamespace(views={"synthetic": cell})


@pytest.mark.parametrize("change", [
    "missing", "duplicate", "extra", "text_swap", "time", "available", "event_ref", "extra_text",
])
def test_projection_binding_rejects_mismatch(change):
    projection, memory = _sample()
    rows, texts = memory.views["synthetic"].rows, memory.views["synthetic"].collection._texts
    if change == "missing":
        rows.pop()
        texts.pop()
    elif change == "duplicate":
        rows[-1] = copy.deepcopy(rows[0])
        texts[-1] = texts[0]
    elif change == "extra":
        rows.append(copy.deepcopy(rows[0]))
        rows[-1]["provenance"]["record"] = "unapproved"
        texts.append(texts[0])
    elif change == "text_swap":
        texts[0] = texts[2]
    elif change == "time":
        rows[0]["time_utc"] = "2026-01-01T01:00:00Z"
    elif change == "available":
        rows[0]["provenance"]["available_utc"] = "2026-01-01T01:00:00Z"
    elif change == "event_ref":
        rows[0]["provenance"]["event_ref"] = "0" * 64
    else:
        texts.append("extra")
    with pytest.raises(checkpoint.CheckpointRefused):
        checkpoint._validate_projection_cell_views(projection, memory)


def test_projection_binding_accepts_exact_views_across_cells():
    projection, memory = _sample()
    first = memory.views["synthetic"]
    second = SimpleNamespace(rows=first.rows[2:], collection=SimpleNamespace(_texts=first.collection._texts[2:]))
    first.rows = first.rows[:2]
    first.collection._texts = first.collection._texts[:2]
    memory.views["second"] = second
    assert checkpoint._validate_projection_cell_views(projection, memory) is None
