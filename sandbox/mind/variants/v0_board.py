"""V0: the current Mind board text (tools/wd_pr_missing_slot_board.py mind view), the same file for every lane."""

from tools import wd_pr_missing_slot_board as board

VARIANT = "V0"


def render(inputs, lane):
    view = board.build_mind_view(inputs["snapshot"], inputs["snapshot_sha256"])
    return {"board.txt": board.render_mind_text(view)}
