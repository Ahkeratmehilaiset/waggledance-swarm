"""Synthetic screened content, lane query and CLI lifecycle; no native process."""

import hashlib
import json
from pathlib import Path

import pytest

from tools import wd_q4_lane_caller_adapter as adapter
from tools import wd_q4_lane_caller_cli as cli
from tools import wd_q4_lane_query
from tests.tools.test_wd_q4_content_lane_query import content, lane
from tests.tools.test_wd_q4_content_release_contract import result_for
from tests.tools.test_wd_q4_content_release_lifecycle import content_main
from tests.tools.test_wd_q4_lane_caller_owner_cli import setup_cli


@pytest.fixture
def screened_main(content_main, lane, monkeypatch):
    binding = cli._configuration()[4]
    binding.clear()
    binding.update(lane["call"]["expected_binding"])

    def call(**kwargs):
        # Real screened parent re-resolution; the underlying transport and
        # encoder are synthetic. Guard tickets model, not prove, native cleanup.
        answer = wd_q4_lane_query.query_public(**dict(lane["call"], lane="codex-lead-1"))
        context = kwargs["_owner_context"]
        for name in ("adapter", "attestation", "transport"):
            ticket = context._ticket(name)
            assert context._accept_ticket(name, ticket) is ticket
            ticket._begin()
            ticket._complete()
        result = result_for(binding)
        result.update(lane=answer["lane"], answer=answer, inner_status=answer["status"],
                      result_count=answer["result_count"], answer_sha256=cli._sha(cli._canon(answer)),
                      identity_sha256=hashlib.sha256(kwargs["identity_bytes"]).hexdigest(),
                      descriptor_sha256=kwargs["expected_descriptor_sha256"])
        return result

    monkeypatch.setattr(adapter, "call_lane_session", call)
    return {**content_main, "lane": lane}


def test_screened_parent_answer_reaches_cli_with_unknown_freshness(screened_main):
    assert cli.main(["--enabled"]) == 0
    assert screened_main["checks"] == ["recheck", "recheck"]
    assert len(screened_main["lane"]["calls"]) == 1
    assert len(screened_main["writes"]) == 1
    output = json.loads(screened_main["writes"][0])
    assert output["schema"] == "wd.q4-lane-content-delivery.v1"
    assert output["freshness"] == "UNKNOWN" and output["index_age"]["status"] == "UNKNOWN"
    assert output["answer"]["results"]


def test_changed_pinned_policy_never_reaches_cli_output(screened_main):
    document = screened_main["lane"]["document"]
    path = Path(document["policy_path"])
    path.write_bytes(path.read_bytes() + b" ")
    assert cli.main(["--enabled"]) != 0
    assert screened_main["writes"] == []
