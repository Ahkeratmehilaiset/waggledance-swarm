"""Synthetic main/guard sequencing for content delivery, not a live query."""

from datetime import timedelta
import hashlib
import json

import pytest

from tools import wd_q4_lane_caller_adapter as adapter
from tools import wd_q4_lane_caller_cli as cli
from tools import wd_q4_guard_completion as guard
from tests.tools.test_wd_q4_lane_caller_owner_cli import NOW, setup_cli
from tests.tools.test_wd_q4_content_release_contract import TEXT, result_for


@pytest.fixture
def content_main(setup_cli, monkeypatch):
    descriptor, raw, descriptor_pin, fleet, binding, inputs = cli._configuration()
    inputs["max_output_bytes"] = 65536
    binding.clear()
    binding.update({key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS})
    monkeypatch.setattr(cli, "_query", lambda: ("synthetic question", NOW))
    calls = []
    monkeypatch.setattr(cli, "_require_current_snapshot", lambda *args: calls.append("recheck"))

    def answer(**kwargs):
        context = kwargs["_owner_context"]
        for name in ("adapter", "attestation", "transport"):
            ticket = context._ticket(name)
            assert context._accept_ticket(name, ticket) is ticket
            ticket._begin()
            ticket._complete()
        result = result_for(binding)
        result["identity_sha256"] = hashlib.sha256(kwargs["identity_bytes"]).hexdigest()
        result["descriptor_sha256"] = kwargs["expected_descriptor_sha256"]
        return result

    monkeypatch.setattr(adapter, "call_lane_session", answer)
    return {**setup_cli, "checks": calls}


def test_content_is_written_only_after_final_snapshot_recheck(content_main):
    assert cli.main(["--enabled"]) == 0
    assert content_main["checks"] == ["recheck", "recheck"]
    assert len(content_main["writes"]) == 1
    result = json.loads(content_main["writes"][0])
    assert result["answer"]["results"][0]["stored_text"] == TEXT
    assert result["freshness"] == "UNKNOWN" and result["index_age"]["status"] == "UNKNOWN"


def test_final_snapshot_failure_emits_no_content(content_main, monkeypatch):
    def recheck(*args):
        content_main["checks"].append("recheck")
        if len(content_main["checks"]) == 2:
            raise ValueError("synthetic mismatch")
    monkeypatch.setattr(cli, "_require_current_snapshot", recheck)
    assert cli.main(["--enabled"]) != 0
    assert content_main["checks"] == ["recheck", "recheck"]
    assert content_main["writes"] == []


def test_unknown_cleanup_emits_no_content(content_main, monkeypatch):
    monkeypatch.setattr(adapter, "call_lane_session", lambda **kwargs: {"status": "UNKNOWN", "cleanup": "UNKNOWN"})
    assert cli.main(["--enabled"]) != 0
    assert content_main["writes"] == []


def test_failed_guard_release_emits_no_content(content_main, monkeypatch):
    def fail(*args):
        raise guard.GuardCompletionUnavailable()
    monkeypatch.setattr(guard._Guard, "release", fail)
    assert cli.main(["--enabled"]) != 0
    assert content_main["writes"] == []


def test_backward_clock_emits_no_content(content_main, monkeypatch):
    times = iter((NOW, NOW - timedelta(seconds=1)))
    monkeypatch.setattr(cli, "_utc_now", lambda: next(times))
    assert cli.main(["--enabled"]) != 0
    assert content_main["writes"] == []
