"""The collector slot record: every slot is connected or reserved, and a reservation can run nothing."""

import json
import re
from pathlib import Path

SLOTS = Path(__file__).resolve().parent / "collector_slots.json"


def load():
    return json.loads(SLOTS.read_text(encoding="utf-8"))


def test_every_slot_is_connected_or_a_reservation_with_a_reason_and_an_unlock():
    record = load()
    assert record["schema"] == "wd.bridge-mind-collector-slots.v1" and record["authority"] == "none"
    names = [slot["name"] for slot in record["slots"]]
    assert len(names) == len(set(names))
    for slot in record["slots"]:
        assert slot["state"] in ("connected", "reserved"), slot["name"]
        if slot["state"] == "connected":
            assert slot["via"] and slot["reads"] and slot["writes"], slot["name"]
        else:
            assert slot["tool"] and slot["reason"] and slot["unlock"], slot["name"]


def test_a_reservation_holds_nothing_that_could_run():
    for slot in load()["slots"]:
        if slot["state"] == "reserved":
            assert not {"argv", "command", "tree", "schedule"} & set(slot), slot["name"]


def test_the_record_names_no_machine_path():
    text = SLOTS.read_text(encoding="utf-8")
    assert not re.search(r"\b[A-Za-z]:[\\/]", text)
    assert "\\Users\\" not in text and "/Users/" not in text
