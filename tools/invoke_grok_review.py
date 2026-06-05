# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed Grok headless review helper.

The helper is intentionally inactive by default. It gives bridge agents a
common command surface for future Grok advisory calls without enabling spend,
scheduled automation, merge authority, or write scope before operator
activation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, MutableMapping, Sequence
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "configs" / "grok_budget.json"
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
FULL_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_FRESHNESS_SHA_FIELDS = (
    "remote_main_sha",
    "local_origin_main_sha",
    "worktree_head",
)
OPTIONAL_FRESHNESS_SHA_FIELDS = (
    "pr_head_sha",
    "reviewed_head_sha",
    "target_head_sha",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "allow_network": False,
    "advisory_only": True,
    "default_model": "grok-code-fast-1",
    "api_key_env": "XAI_API_KEY",
    "api_base_url_env": "GROK_API_BASE_URL",
    "chat_completions_path": "/v1/chat/completions",
    "max_calls_per_day": 0,
    "max_prompt_bytes": 12000,
    "max_response_bytes": 20000,
    "timeout_sec": 60,
}


class GrokReviewError(ValueError):
    """Raised when a Grok review invocation must fail closed."""

    def __init__(self, report: dict[str, Any], *, exit_code: int = 2) -> None:
        super().__init__(str(report.get("decision", "grok_review_error")))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Invoke Grok advisory review.")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", default=None)
    prompt_group.add_argument("--prompt-file", type=Path, default=None)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--now", default="")
    parser.add_argument("--require-freshness", action="store_true")
    parser.add_argument("--remote-main-sha", default="")
    parser.add_argument("--local-origin-main-sha", default="")
    parser.add_argument("--worktree-head", default="")
    parser.add_argument("--pr-head-sha", default="")
    parser.add_argument("--reviewed-head-sha", default="")
    parser.add_argument("--target-head-sha", default="")
    parser.add_argument("--git-root", type=Path, default=ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        prompt = _read_prompt(args.prompt, args.prompt_file)
        now = _parse_now(args.now)
        freshness = _freshness_from_args(args)
        report = run_grok_review(
            prompt=prompt,
            task_id=args.task_id,
            agent=args.agent,
            model=args.model or None,
            config_path=args.config,
            state_path=args.state,
            dry_run=args.dry_run,
            now=now,
            env=os.environ,
            require_freshness=args.require_freshness,
            freshness=freshness,
            git_root=args.git_root,
        )
    except GrokReviewError as exc:
        report = exc.report
        exit_code = exc.exit_code
    except OSError as exc:
        report = _base_report(
            ok=False,
            decision="grok_review_error",
            reason=exc.__class__.__name__,
        )
        exit_code = 1
    else:
        exit_code = 0 if report.get("ok") else 3

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def run_grok_review(
    *,
    prompt: str,
    task_id: str = "",
    agent: str = "",
    model: str | None = None,
    config_path: Path | str | None = DEFAULT_CONFIG_PATH,
    state_path: Path | str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    require_freshness: bool = False,
    freshness: Mapping[str, Any] | None = None,
    git_root: Path | str | None = ROOT,
) -> dict[str, Any]:
    """Run or preflight one advisory review request.

    ``dry_run`` validates the request and reports whether an actual call would
    be allowed, but it never reads secrets, writes budget state, or opens the
    network. Actual Grok network calls always require a validated freshness
    proof; ``require_freshness`` also applies that preflight to disabled and
    dry-run paths.
    """
    env = env or {}
    now = now or datetime.now(timezone.utc)
    config = load_grok_config(config_path)
    selected_model = model or str(config["default_model"])
    prompt_bytes = prompt.encode("utf-8")
    prompt_sha = hashlib.sha256(prompt_bytes).hexdigest()
    _validate_prompt(prompt, prompt_bytes, config)
    freshness_proof = _validate_freshness_proof(
        freshness,
        required=require_freshness,
        git_root=git_root,
    )

    state = _load_state(_resolve_state_path(state_path, env), now)
    budget = _budget_snapshot(config, state, selected_model)
    active_gate = _active_gate(config, budget)
    freshness_error_report: dict[str, Any] | None = None
    if bool(active_gate["ok"]) and freshness_proof is not None and not require_freshness:
        try:
            freshness_proof = _validate_freshness_proof(
                freshness,
                required=True,
                git_root=git_root,
            )
        except GrokReviewError as exc:
            freshness_error_report = exc.report
    freshness_required = require_freshness or bool(active_gate["ok"])
    freshness_missing = freshness_required and freshness_proof is None
    report = _base_report(
        ok=True,
        decision="dry_run_ready" if dry_run else "grok_review_ready",
        reason="",
        task_id=task_id,
        agent=agent,
        model=selected_model,
        prompt_sha256=prompt_sha,
        prompt_bytes=len(prompt_bytes),
        advisory_only=bool(config["advisory_only"]),
        network_attempted=False,
        budget=budget,
        dry_run=dry_run,
        would_call=(
            bool(active_gate["ok"])
            and not freshness_missing
            and freshness_error_report is None
        ),
        freshness_required=freshness_required,
        freshness=freshness_proof or {},
    )
    if dry_run:
        if freshness_missing:
            report["would_refuse_reason"] = "freshness proof required"
        if freshness_error_report is not None:
            report["would_refuse_reason"] = str(
                freshness_error_report.get("reason", "freshness proof required")
            )
        if not active_gate["ok"]:
            report["would_refuse_reason"] = active_gate["reason"]
        return report

    if freshness_missing:
        report.update(
            ok=False,
            decision="missing_freshness_proof",
            reason="freshness proof required before Grok network call",
        )
        return report

    if freshness_error_report is not None:
        report.update(
            ok=False,
            decision=freshness_error_report.get("decision", "stale_freshness_proof"),
            reason=freshness_error_report.get("reason", "freshness proof required"),
        )
        return report

    if not active_gate["ok"]:
        report.update(
            ok=False,
            decision=active_gate["decision"],
            reason=active_gate["reason"],
        )
        return report

    api_key_env = str(config["api_key_env"])
    api_base_url_env = str(config["api_base_url_env"])
    api_key = str(env.get(api_key_env, "")).strip()
    api_base_url = str(env.get(api_base_url_env, "")).strip()
    if not api_key:
        report.update(ok=False, decision="missing_api_key", reason=api_key_env)
        return report
    if not api_base_url:
        report.update(
            ok=False,
            decision="missing_api_base_url",
            reason=api_base_url_env,
        )
        return report

    response_text = _call_grok(
        api_base_url=api_base_url,
        api_key=api_key,
        model=selected_model,
        prompt=prompt,
        config=config,
    )
    _record_budget(_resolve_state_path(state_path, env), state, now, selected_model)
    report.update(
        ok=True,
        decision="grok_advisory_received",
        network_attempted=True,
        response_sha256=hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
        response_bytes=len(response_text.encode("utf-8")),
        response_text=response_text,
        would_call=True,
    )
    return report


def load_grok_config(path: Path | str | None) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if path is not None:
        p = Path(path)
        if p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise GrokReviewError(
                    _base_report(
                        ok=False,
                        decision="invalid_config",
                        reason="config JSON is malformed",
                    )
                ) from exc
            if not isinstance(raw, dict):
                raise GrokReviewError(
                    _base_report(
                        ok=False,
                        decision="invalid_config",
                        reason="config must be a JSON object",
                    )
                )
            config.update(raw)
    config["enabled"] = _bool_config(config, "enabled")
    config["allow_network"] = _bool_config(config, "allow_network")
    config["advisory_only"] = _bool_config(config, "advisory_only")
    config["max_calls_per_day"] = _int_config(config, "max_calls_per_day")
    config["max_prompt_bytes"] = _int_config(config, "max_prompt_bytes")
    config["max_response_bytes"] = _int_config(config, "max_response_bytes")
    config["timeout_sec"] = _int_config(config, "timeout_sec")
    for key in (
        "default_model",
        "api_key_env",
        "api_base_url_env",
        "chat_completions_path",
    ):
        config[key] = str(config.get(key, DEFAULT_CONFIG[key])).strip()
    if not config["advisory_only"]:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_config",
                reason="Grok helper must remain advisory_only",
            )
        )
    return config


def _read_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt_file is not None:
        return prompt_file.read_text(encoding="utf-8")
    return prompt or ""


def _validate_prompt(
    prompt: str, prompt_bytes: bytes, config: Mapping[str, Any]
) -> None:
    if not prompt.strip():
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_prompt",
                reason="prompt must not be empty",
            )
        )
    for marker in PRIVATE_MARKERS:
        if marker.lower() in prompt.lower():
            raise GrokReviewError(
                _base_report(
                    ok=False,
                    decision="private_marker_refused",
                    reason=marker,
                )
            )
    max_prompt_bytes = int(config["max_prompt_bytes"])
    if len(prompt_bytes) > max_prompt_bytes:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="prompt_too_large",
                reason=f"{len(prompt_bytes)} > {max_prompt_bytes}",
            )
        )


def _freshness_from_args(args: argparse.Namespace) -> dict[str, str] | None:
    freshness = {
        "remote_main_sha": str(args.remote_main_sha).strip(),
        "local_origin_main_sha": str(args.local_origin_main_sha).strip(),
        "worktree_head": str(args.worktree_head).strip(),
        "pr_head_sha": str(args.pr_head_sha).strip(),
        "reviewed_head_sha": str(args.reviewed_head_sha).strip(),
        "target_head_sha": str(args.target_head_sha).strip(),
    }
    freshness = {key: value for key, value in freshness.items() if value}
    return freshness or None


def _validate_freshness_proof(
    freshness: Mapping[str, Any] | None,
    *,
    required: bool,
    git_root: Path | str | None,
) -> dict[str, Any] | None:
    if freshness is None:
        if required:
            raise GrokReviewError(
                _base_report(
                    ok=False,
                    decision="missing_freshness_proof",
                    reason="freshness proof required",
                )
            )
        return None
    if not isinstance(freshness, Mapping):
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_freshness_proof",
                reason="freshness proof must be an object",
            )
        )

    proof: dict[str, Any] = {"freshness_ok": True}
    freshness_ok = freshness.get("freshness_ok")
    if freshness_ok is not None and freshness_ok is not True:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="stale_freshness_proof",
                reason="freshness_ok must be true",
            )
        )
    for field_name in REQUIRED_FRESHNESS_SHA_FIELDS:
        value = freshness.get(field_name)
        if not _is_full_git_sha(value):
            raise GrokReviewError(
                _base_report(
                    ok=False,
                    decision="invalid_freshness_proof",
                    reason=f"{field_name} must be lowercase 40-hex sha",
                )
            )
        proof[field_name] = str(value)

    if proof["remote_main_sha"] != proof["local_origin_main_sha"]:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="stale_freshness_proof",
                reason="remote_main_sha must match local_origin_main_sha",
            )
        )
    if proof["worktree_head"] != proof["local_origin_main_sha"]:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="stale_freshness_proof",
                reason="worktree_head must match local_origin_main_sha",
            )
        )

    if git_root is not None:
        git_origin_main = _git_rev_parse(git_root, "origin/main")
        git_head = _git_rev_parse(git_root, "HEAD")
        if proof["local_origin_main_sha"] != git_origin_main:
            raise GrokReviewError(
                _base_report(
                    ok=False,
                    decision="stale_freshness_proof",
                    reason="local_origin_main_sha must match git origin/main",
                )
            )
        if proof["worktree_head"] != git_head:
            raise GrokReviewError(
                _base_report(
                    ok=False,
                    decision="stale_freshness_proof",
                    reason="worktree_head must match git HEAD",
                )
            )

    exact_head_values = {
        field_name: freshness.get(field_name)
        for field_name in OPTIONAL_FRESHNESS_SHA_FIELDS
    }
    exact_head_present = any(
        value not in (None, "") for value in exact_head_values.values()
    )
    if required or exact_head_present:
        missing = [
            field_name
            for field_name, value in exact_head_values.items()
            if value in (None, "")
        ]
        if missing:
            raise GrokReviewError(
                _base_report(
                    ok=False,
                    decision="missing_freshness_proof",
                    reason=(
                        "pr_head_sha, reviewed_head_sha, target_head_sha "
                        "must all be provided"
                    ),
                )
            )
        exact_shas: list[str] = []
        for field_name, value in exact_head_values.items():
            if not _is_full_git_sha(value):
                raise GrokReviewError(
                    _base_report(
                        ok=False,
                        decision="invalid_freshness_proof",
                        reason=f"{field_name} must be lowercase 40-hex sha",
                    )
                )
            proof[field_name] = str(value)
            exact_shas.append(str(value))
        if len(set(exact_shas)) != 1:
            raise GrokReviewError(
                _base_report(
                    ok=False,
                    decision="stale_freshness_proof",
                    reason=(
                        "pr_head_sha, reviewed_head_sha, target_head_sha "
                        "must match"
                    ),
                )
            )

    return proof


def _is_full_git_sha(value: object) -> bool:
    return isinstance(value, str) and bool(FULL_GIT_SHA_RE.fullmatch(value))


def _git_rev_parse(git_root: Path | str, ref: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", ref],
        check=False,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not _is_full_git_sha(value):
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_freshness_proof",
                reason=f"git rev-parse failed for {ref}",
            )
        )
    return value


def _active_gate(
    config: Mapping[str, Any], budget: Mapping[str, Any]
) -> dict[str, Any]:
    if not bool(config["enabled"]):
        return {
            "ok": False,
            "decision": "grok_disabled",
            "reason": "config enabled=false",
        }
    if not bool(config["allow_network"]):
        return {
            "ok": False,
            "decision": "network_disabled",
            "reason": "config allow_network=false",
        }
    if not bool(budget["can_call"]):
        return {
            "ok": False,
            "decision": "grok_budget_exhausted",
            "reason": "daily call cap reached",
        }
    return {"ok": True, "decision": "ready", "reason": ""}


def _resolve_state_path(
    state_path: Path | str | None,
    env: Mapping[str, str],
) -> Path | None:
    if state_path is not None:
        return Path(state_path)
    root = str(env.get("AGENT_BRIDGE_RUNTIME_ROOT", "")).strip()
    if not root:
        return None
    return Path(root) / "grok_budget_state.json"


def _load_state(path: Path | None, now: datetime) -> dict[str, Any]:
    date_utc = now.date().isoformat()
    if path is None or not path.is_file():
        return {"date_utc": date_utc, "total_calls": 0, "calls_by_model": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_state",
                reason="budget state JSON is malformed",
            )
        ) from exc
    if not isinstance(raw, dict) or raw.get("date_utc") != date_utc:
        return {"date_utc": date_utc, "total_calls": 0, "calls_by_model": {}}
    calls_by_model = raw.get("calls_by_model")
    if not isinstance(calls_by_model, dict):
        calls_by_model = {}
    try:
        return {
            "date_utc": date_utc,
            "total_calls": int(raw.get("total_calls", 0)),
            "calls_by_model": {str(k): int(v) for k, v in calls_by_model.items()},
        }
    except (TypeError, ValueError) as exc:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_state",
                reason="budget state counters must be integers",
            )
        ) from exc


def _budget_snapshot(
    config: Mapping[str, Any],
    state: Mapping[str, Any],
    model: str,
) -> dict[str, Any]:
    max_calls = int(config["max_calls_per_day"])
    calls_today = int(state.get("total_calls", 0))
    calls_by_model = dict(state.get("calls_by_model") or {})
    return {
        "max_calls_per_day": max_calls,
        "calls_today": calls_today,
        "model_calls_today": int(calls_by_model.get(model, 0)),
        "can_call": max_calls > 0 and calls_today < max_calls,
    }


def _record_budget(
    path: Path | None,
    state: MutableMapping[str, Any],
    now: datetime,
    model: str,
) -> None:
    if path is None:
        return
    calls_by_model = dict(state.get("calls_by_model") or {})
    calls_by_model[model] = int(calls_by_model.get(model, 0)) + 1
    updated = {
        "date_utc": now.date().isoformat(),
        "total_calls": int(state.get("total_calls", 0)) + 1,
        "calls_by_model": calls_by_model,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, sort_keys=True) + "\n", encoding="utf-8")


def _call_grok(
    *,
    api_base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    config: Mapping[str, Any],
) -> str:
    url = api_base_url.rstrip("/") + str(config["chat_completions_path"])
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an advisory reviewer for WaggleDance. "
                    "You have no write scope and no merge authority. "
                    "Return concise findings with current-head freshness."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - explicit operator-gated URL.
            request,
            timeout=int(config["timeout_sec"]),
        ) as response:
            raw = response.read(int(config["max_response_bytes"]) + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="grok_call_failed",
                reason=exc.__class__.__name__,
                network_attempted=True,
            ),
            exit_code=4,
        ) from exc
    if len(raw) > int(config["max_response_bytes"]):
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="response_too_large",
                reason="max_response_bytes exceeded",
                network_attempted=True,
            ),
            exit_code=4,
        )
    parsed = json.loads(raw.decode("utf-8"))
    try:
        return str(parsed["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_grok_response",
                reason=exc.__class__.__name__,
                network_attempted=True,
            ),
            exit_code=4,
        ) from exc


def _parse_now(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bool_config(config: Mapping[str, Any], key: str) -> bool:
    raw = config.get(key, DEFAULT_CONFIG[key])
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def _int_config(config: Mapping[str, Any], key: str) -> int:
    value = int(config.get(key, DEFAULT_CONFIG[key]))
    if value < 0:
        raise GrokReviewError(
            _base_report(
                ok=False,
                decision="invalid_config",
                reason=f"{key} must be non-negative",
            )
        )
    return value


def _base_report(**overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "decision": "grok_review_refused",
        "reason": "",
        "task_id": "",
        "agent": "",
        "model": "",
        "prompt_sha256": "",
        "prompt_bytes": 0,
        "advisory_only": True,
        "network_attempted": False,
        "dry_run": False,
        "would_call": False,
        "budget": {},
        "freshness_required": False,
        "freshness": {},
    }
    report.update(overrides)
    return report


def _print_human(report: Mapping[str, Any]) -> None:
    status = "OK" if report.get("ok") else "REFUSED"
    print(f"{status}: {report.get('decision')}")
    reason = report.get("reason")
    if reason:
        print(f"reason: {reason}")
    if report.get("dry_run"):
        print(f"would_call: {bool(report.get('would_call'))}")


if __name__ == "__main__":
    raise SystemExit(main())
