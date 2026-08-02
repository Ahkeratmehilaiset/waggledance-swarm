# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import pytest

import tools.run_open_world_understanding_v1 as harness
from waggledance.core.learning.understanding_contracts import HexCellAddressV1
from waggledance.core.learning.understanding_loop import (
    UnderstandingLoop,
    UnderstandingLoopError,
    UnderstandingPolicyV1,
)
from waggledance.core.magma.understanding_ledger import UnderstandingLedger


def _cell() -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id="adversarial-domain",
        q=0,
        r=0,
        incarnation_id="inc-adversarial",
        generation=1,
        fence=1,
    )


def _observation(source_seq: int, value: float, unit: str = "Cel") -> dict:
    return {
        "observation_id": f"adversarial-{source_seq}",
        "source_seq": source_seq,
        "source": "mqtt",
        "entity_id": "wd.synthetic.adversarial",
        "metric": "temperature",
        "unit": unit,
        "value": value,
        "quality": 0.9,
        "privacy_class": "synthetic",
        "metadata": {},
    }


def test_harness_never_uses_network_or_subprocess(tmp_path, monkeypatch) -> None:
    def refused(*args, **kwargs):
        raise AssertionError("external execution is outside the harness boundary")

    monkeypatch.setattr(socket, "socket", refused)
    monkeypatch.setattr(subprocess, "run", refused)
    monkeypatch.setattr(subprocess, "Popen", refused)

    report = harness.build_acceptance_report(
        scratch_dir=tmp_path,
        generated_at_utc=harness.FIXED_NOW,
    )

    assert report["ok"] is True


def test_harness_artifact_cannot_be_switched_on_by_environment(
    tmp_path, monkeypatch
) -> None:
    for name in (
        "WAGGLE_RUNTIME_AUTHORITY",
        "WAGGLE_ROUTING_INFLUENCE",
        "WAGGLE_BUILDER_AUTHORITY",
        "WAGGLE_CLAIM_SAFE",
    ):
        monkeypatch.setenv(name, "true")

    report = harness.build_acceptance_report(
        scratch_dir=tmp_path,
        generated_at_utc=harness.FIXED_NOW,
    )

    for gate in harness.CLAIM_GATES:
        assert report[gate] is False


def test_semantic_learning_domain_change_cannot_reuse_numeric_state(
    tmp_path,
) -> None:
    path = tmp_path / "domain-drift.db"
    first = UnderstandingLoop(
        cell=_cell(),
        event_sink=UnderstandingLedger(path),
        policy=UnderstandingPolicyV1(unit="Cel"),
        recover_from_verified_ledger=True,
    )
    ticket = first.prepare_observation(_observation(1, 12.0))
    first.complete_numeric(ticket, 12.0)
    first.close()

    changed_ledger = UnderstandingLedger(path)
    try:
        with pytest.raises(
            UnderstandingLoopError,
            match="ledger learning domain differs from configured policy",
        ):
            UnderstandingLoop(
                cell=_cell(),
                event_sink=changed_ledger,
                policy=UnderstandingPolicyV1(unit="K"),
                recover_from_verified_ledger=True,
            )
    finally:
        changed_ledger.close()


def test_cell_fence_change_requires_explicit_new_ledger(tmp_path) -> None:
    path = tmp_path / "fence-drift.db"
    first = UnderstandingLoop(
        cell=_cell(),
        event_sink=UnderstandingLedger(path),
        recover_from_verified_ledger=True,
    )
    ticket = first.prepare_observation(_observation(1, 12.0))
    first.complete_numeric(ticket, 12.0)
    first.close()
    rebuilt = HexCellAddressV1(
        cell_id="adversarial-domain",
        q=0,
        r=0,
        incarnation_id="inc-rebuilt",
        generation=2,
        fence=2,
    )

    changed_ledger = UnderstandingLedger(path)
    try:
        with pytest.raises(
            UnderstandingLoopError,
            match="ledger learning domain differs from configured policy",
        ):
            UnderstandingLoop(
                cell=rebuilt,
                event_sink=changed_ledger,
                recover_from_verified_ledger=True,
            )
    finally:
        changed_ledger.close()


def test_failure_is_not_emitted_as_a_partial_success_artifact(
    tmp_path, monkeypatch, capsys
) -> None:
    def fail_wdp():
        raise harness.AcceptanceHarnessError("forced WDP failure")

    monkeypatch.setattr(harness, "_exercise_wdp", fail_wdp)

    result = harness.main(
        [
            "--scratch-root",
            str(tmp_path),
            "--now",
            harness.FIXED_NOW,
            "--json",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert "acceptance proof failed" in captured.err
    assert '"ok": true' not in captured.err


def test_report_contains_only_aggregate_evidence(tmp_path) -> None:
    report = harness.build_acceptance_report(
        scratch_dir=tmp_path,
        generated_at_utc=harness.FIXED_NOW,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert harness.SYNTHETIC_SECRET_MARKER not in serialized
    assert "acceptance-observation" not in serialized
    assert "wd.synthetic.acceptance-hive" not in serialized
    assert "hmac-sha256:" not in serialized
    assert str(Path(tmp_path)) not in serialized


def test_direct_report_builder_rejects_noncanonical_or_injected_time(
    tmp_path,
) -> None:
    with pytest.raises(ValueError, match="canonical UTC"):
        harness.build_acceptance_report(
            scratch_dir=tmp_path,
            generated_at_utc="operator-secret-in-generated-at",
        )
