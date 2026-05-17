from __future__ import annotations

from datetime import datetime, timedelta

from waggledance.core.pdam_close_solver import (
    LogbookEntry,
    MesComment,
    ToolState,
    build_tool_graph,
    plan_close_actions,
)


BASE = datetime(2026, 5, 15, 8, 0)


def entry(
    entry_id: int,
    device: str,
    status: str,
    created_at: datetime,
    local_id: int | None = None,
) -> LogbookEntry:
    return LogbookEntry(
        entry_id=entry_id,
        local_id=local_id or entry_id,
        log_code="em-repair-wp1",
        device=device,
        status=status,
        created_at=created_at,
        issue="pump issue",
    )


def comment(tool_id: str, offset_hours: int, text: str, by_user: str = "HSS") -> MesComment:
    return MesComment(
        tool_id=tool_id,
        when=BASE + timedelta(hours=offset_hours),
        by_user=by_user,
        comment=text,
    )


def test_build_tool_graph_uses_prefix_discovery_and_manual_hints() -> None:
    graph = build_tool_graph(
        parents=["SPUT_02", "ODD_PARENT"],
        all_tool_ids=["SPUT_02", "SPUT_02_DEPA", "SPUT_02_DEPB", "OTHER_01"],
        manual_hints={"ODD_PARENT": ["LEGACY_CHAMBER_A"]},
    )

    assert graph["SPUT_02"] == ["SPUT_02_DEPA", "SPUT_02_DEPB"]
    assert graph["ODD_PARENT"] == ["LEGACY_CHAMBER_A"]


def test_ok_like_parent_and_subtools_close_with_time_windowed_evidence() -> None:
    rows = [
        entry(1001, "SPUT_02", "EQ_STOP", BASE),
        entry(1002, "SPUT_02", "EQ_STOP", BASE + timedelta(hours=8)),
    ]
    actions = plan_close_actions(
        entries=[rows[0]],
        repair_timeline=rows,
        tool_states={
            "SPUT_02": ToolState("SPUT_02", "IDLE"),
            "SPUT_02_DEPB": ToolState("SPUT_02_DEPB", "PRODUCTION"),
        },
        comments=[
            comment("SPUT_02_DEPB", 1, "Cryo pump changed and chamber recovered."),
            comment("SPUT_02_DEPB", 9, "Later unrelated chamber comment must not leak."),
        ],
        subtools={"SPUT_02": ["SPUT_02_DEPB"]},
    )

    assert [a.kind for a in actions] == ["CLOSE_OK"]
    assert "Cryo pump changed" in actions[0].action_text
    assert "[SPUT_02_DEPB]" in actions[0].action_text
    assert "Later unrelated" not in actions[0].action_text


def test_limited_subtool_keeps_existing_wip_and_closes_same_incident_duplicate() -> None:
    rows = [
        entry(2001, "SPUT_02", "WIP", BASE + timedelta(hours=2), local_id=20),
        entry(2000, "SPUT_02", "EQ_STOP", BASE, local_id=19),
    ]
    actions = plan_close_actions(
        entries=rows,
        repair_timeline=rows,
        tool_states={
            "SPUT_02": ToolState("SPUT_02", "IDLE"),
            "SPUT_02_DEPB": ToolState("SPUT_02_DEPB", "ENGINEERING", comment="DepB still locked"),
        },
        comments=[comment("SPUT_02_DEPB", 1, "Work continues on DepB chamber.")],
        subtools={"SPUT_02": ["SPUT_02_DEPB"]},
    )

    by_id = {a.entry_id: a for a in actions}
    assert by_id[2001].kind == "KEEP_WIP"
    assert by_id[2001].target_status == "WIP"
    assert by_id[2000].kind == "CLOSE_DUPLICATE"
    assert by_id[2000].duplicate_of == 2001


def test_limited_subtool_marks_non_duplicate_fault_for_review() -> None:
    rows = [
        entry(3001, "SPUT_02", "WIP", BASE + timedelta(days=3), local_id=31),
        entry(3000, "SPUT_02", "EQ_STOP", BASE, local_id=30),
    ]
    actions = plan_close_actions(
        entries=rows,
        repair_timeline=rows,
        tool_states={
            "SPUT_02": ToolState("SPUT_02", "IDLE"),
            "SPUT_02_DEPB": ToolState("SPUT_02_DEPB", "DOWNTIME"),
        },
        comments=[comment("SPUT_02_DEPB", 1, "Older work note.")],
        subtools={"SPUT_02": ["SPUT_02_DEPB"]},
        duplicate_window_hours=24,
    )

    by_id = {a.entry_id: a for a in actions}
    assert by_id[3001].kind == "KEEP_WIP"
    assert by_id[3000].kind == "REVIEW"
    assert by_id[3000].target_status == "REVIEW"


def test_automatic_and_trivial_comments_are_filtered_from_evidence() -> None:
    actions = plan_close_actions(
        entries=[entry(4000, "DRIE_05B", "EQ_STOP", BASE)],
        repair_timeline=[entry(4000, "DRIE_05B", "EQ_STOP", BASE)],
        tool_states={"DRIE_05B": ToolState("DRIE_05B", "PRODUCTION")},
        comments=[
            comment("DRIE_05B", 1, "Automatic tool state transition performed."),
            comment("DRIE_05B", 2, "ok"),
            comment("DRIE_05B", 3, "RTP completed and tool released to production.", "HENH"),
        ],
        subtools={},
    )

    assert "Automatic tool state" not in actions[0].action_text
    assert "RTP completed" in actions[0].action_text
