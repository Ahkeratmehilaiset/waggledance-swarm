# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import waggledance.bootstrap.container as container_module
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import (
    Container,
    _build_open_world_understanding_loop,
    _resolve_open_world_ledger_path,
)
from waggledance.core.learning.understanding_loop import (
    InMemoryUnderstandingEventSink,
    PLAINTEXT_REVEAL_RETENTION_POLICY_V1,
    UnderstandingLoop,
    UnderstandingLoopError,
)
from waggledance.core.magma.understanding_ledger import UnderstandingLedger


def _settings(config: dict | None = None) -> WaggleSettings:
    extras = (
        {"open_world_understanding": dict(config)}
        if config is not None
        else {}
    )
    return WaggleSettings(profile="TEST", _extras=extras)


def _shadow_config(**overrides) -> dict:
    return {
        "mode": "shadow",
        "reveal_retention_policy": PLAINTEXT_REVEAL_RETENTION_POLICY_V1,
        **overrides,
    }


def _observation(source_seq: int, value: float) -> dict:
    return {
        "observation_id": f"container-obs-{source_seq}",
        "source_seq": source_seq,
        "source": "mqtt",
        "entity_id": "wd.synthetic.container-hive",
        "metric": "temperature",
        "unit": "Cel",
        "value": value,
        "quality": 0.9,
        "privacy_class": "synthetic",
        "metadata": {"fixture": True},
    }


def test_shipped_configuration_keeps_open_world_understanding_off() -> None:
    settings_path = Path(__file__).parents[2] / "configs" / "settings.yaml"
    document = yaml.safe_load(settings_path.read_text(encoding="utf-8"))

    assert document["open_world_understanding"]["mode"] == "off"
    assert "reveal_retention_policy" not in document["open_world_understanding"]


def test_settings_loader_and_builder_keep_shipped_configuration_off() -> None:
    settings = WaggleSettings.from_env()

    assert settings.get("open_world_understanding.mode") == "off"
    assert _build_open_world_understanding_loop(settings, stub=False) is None


def test_default_off_returns_before_creating_a_ledger(tmp_path) -> None:
    ledger_path = tmp_path / "must-not-exist.db"
    settings = _settings({"mode": "off", "ledger_path": str(ledger_path)})

    loop = _build_open_world_understanding_loop(settings, stub=False)

    assert loop is None
    assert not ledger_path.exists()


@pytest.mark.parametrize("section", (None, True, "shadow", ["shadow"]))
def test_malformed_open_world_section_fails_closed(section) -> None:
    settings = WaggleSettings(
        profile="TEST",
        _extras={"open_world_understanding": section},
    )

    with pytest.raises(ValueError, match="must be a configuration mapping"):
        _build_open_world_understanding_loop(settings, stub=False)


def test_relative_ledger_path_is_project_root_anchored(
    tmp_path, monkeypatch
) -> None:
    project_root = tmp_path / "persistent-project"
    project_root.mkdir()
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    monkeypatch.setattr(
        container_module,
        "_OPEN_WORLD_PROJECT_ROOT",
        project_root,
    )
    monkeypatch.chdir(other_cwd)

    resolved = _resolve_open_world_ledger_path("data/understanding.db")

    assert resolved == project_root / "data" / "understanding.db"


def test_relative_ledger_path_cannot_escape_project_root(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        container_module,
        "_OPEN_WORLD_PROJECT_ROOT",
        tmp_path / "persistent-project",
    )

    with pytest.raises(ValueError, match="must stay inside the project root"):
        _resolve_open_world_ledger_path("../outside.db")


def test_explicit_shadow_stub_uses_only_an_ephemeral_sink() -> None:
    loop = _build_open_world_understanding_loop(
        _settings(_shadow_config()),
        stub=True,
    )

    assert type(loop) is UnderstandingLoop
    assert type(loop.event_sink) is InMemoryUnderstandingEventSink
    assert loop.stats()["runtime_authority_applied"] is False
    assert loop.stats()["routing_influence_applied"] is False


def test_explicit_nonstub_shadow_uses_durable_restart_replay(tmp_path) -> None:
    ledger_path = tmp_path / "understanding.db"
    settings = _settings(
        _shadow_config(ledger_path=str(ledger_path))
    )
    first = _build_open_world_understanding_loop(settings, stub=False)
    assert type(first.event_sink) is UnderstandingLedger
    ticket = first.prepare_observation(_observation(1, 12.0))
    first.complete_numeric(ticket, 12.0)
    first.close()

    second = _build_open_world_understanding_loop(settings, stub=False)
    next_ticket = second.prepare_observation(_observation(2, 14.0))

    assert next_ticket.prediction is not None
    assert next_ticket.prediction.ingest_seq == 2
    assert next_ticket.prediction.predicted_value == 12.0
    second.close()


def test_durable_restart_rejects_incompatible_learning_domain(tmp_path) -> None:
    ledger_path = tmp_path / "understanding-domain.db"
    first = _build_open_world_understanding_loop(
        _settings(
            _shadow_config(
                ledger_path=str(ledger_path),
                unit="Cel",
            )
        ),
        stub=False,
    )
    ticket = first.prepare_observation(_observation(1, 12.0))
    first.complete_numeric(ticket, 12.0)
    first.close()

    with pytest.raises(
        UnderstandingLoopError,
        match="ledger learning domain differs from configured policy",
    ):
        _build_open_world_understanding_loop(
            _settings(
                _shadow_config(
                    ledger_path=str(ledger_path),
                    unit="K",
                )
            ),
            stub=False,
        )


@pytest.mark.parametrize("mode", (None, True, "SHADOW", "candidate", ""))
def test_unknown_modes_fail_closed_without_creating_a_ledger(
    tmp_path,
    mode,
) -> None:
    ledger_path = tmp_path / "invalid-mode.db"

    with pytest.raises(ValueError, match="literal 'off' or 'shadow'"):
        _build_open_world_understanding_loop(
            _settings({"mode": mode, "ledger_path": str(ledger_path)}),
            stub=False,
        )

    assert not ledger_path.exists()


def test_invalid_shadow_policy_fails_before_ledger_construction(tmp_path) -> None:
    ledger_path = tmp_path / "invalid-policy.db"

    with pytest.raises(ValueError, match="max_targets"):
        _build_open_world_understanding_loop(
            _settings(
                _shadow_config(
                    ledger_path=str(ledger_path),
                    max_targets=True,
                )
            ),
            stub=False,
        )

    assert not ledger_path.exists()


@pytest.mark.parametrize(
    "retention_policy",
    (None, "", "ttl", "redact", "encrypt", True),
)
def test_shadow_requires_literal_plaintext_retention_acknowledgement(
    tmp_path,
    retention_policy,
) -> None:
    ledger_path = tmp_path / "retention-policy-refused.db"

    with pytest.raises(ValueError, match="reveal_retention_policy must be literal"):
        _build_open_world_understanding_loop(
            _settings(
                {
                    "mode": "shadow",
                    "ledger_path": str(ledger_path),
                    "reveal_retention_policy": retention_policy,
                }
            ),
            stub=False,
        )

    assert not ledger_path.exists()


def test_changing_only_shipped_mode_to_shadow_fails_before_ledger(tmp_path) -> None:
    ledger_path = tmp_path / "missing-retention-acknowledgement.db"

    with pytest.raises(ValueError, match="reveal_retention_policy must be literal"):
        _build_open_world_understanding_loop(
            _settings({"mode": "shadow", "ledger_path": str(ledger_path)}),
            stub=False,
        )

    assert not ledger_path.exists()


def test_container_passes_only_explicit_shadow_loop_to_runtime() -> None:
    off_runtime = Container(settings=_settings(), stub=True).autonomy_service._runtime
    shadow_runtime = Container(
        settings=_settings(_shadow_config()),
        stub=True,
    ).autonomy_service._runtime

    assert off_runtime.understanding_loop is None
    assert type(shadow_runtime.understanding_loop) is UnderstandingLoop
    stats = shadow_runtime.understanding_shadow_stats()
    assert stats["enabled"] is True
    assert stats["runtime_authority_applied"] is False
    assert stats["routing_influence_applied"] is False
    shadow_runtime.understanding_loop.close()


def test_container_closes_understanding_loop_when_runtime_construction_fails(
    monkeypatch,
) -> None:
    class CloseProbe:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class FailingRuntime:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("forced runtime construction failure")

    probe = CloseProbe()
    monkeypatch.setattr(
        container_module,
        "_build_open_world_understanding_loop",
        lambda settings, *, stub: probe,
    )
    import waggledance.core.autonomy.runtime as runtime_module

    monkeypatch.setattr(runtime_module, "AutonomyRuntime", FailingRuntime)

    with pytest.raises(RuntimeError, match="forced runtime construction failure"):
        Container(settings=_settings(), stub=True).autonomy_service

    assert probe.close_calls == 1
