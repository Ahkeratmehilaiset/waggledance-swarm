"""Synthetic tests for the default-off useful work content projection (bridge messages, increment 1).

No real bridge row, message, secret or sealed material is used. Every canary is assembled from parts at test time so
the repository and receipts never hold a scanner-shaped literal. Non-ASCII text is written with escapes.
"""
import hashlib
import json
import logging
import re

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from tools.bridge_compact_view import event_id

LEAD, TOOLS, FABLE = "codex-lead-1", "codex-tools-1", "fable-5"
RCO1, RCO2 = "claude-rco-1", "claude-rco-2"
ROSTER = {LEAD: "00000001-0000-4000-8000-000000000000", TOOLS: "00000002-0000-4000-8000-000000000000",
          FABLE: "00000005-0000-4000-8000-000000000000", RCO1: "00000003-0000-4000-8000-000000000000",
          RCO2: "00000004-0000-4000-8000-000000000000"}
TASK = "synthetic-content-task-v1"
FINNISH = "Testit meniv\u00e4t l\u00e4pi ja yhdist\u00e4minen onnistui, seuraavaksi k\u00e4ytt\u00f6\u00f6notto."
TEXT_CLASS = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")  # the lane output class every hop enforces
HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")


def policy_doc(**changes):
    doc = {"schema": cp.POLICY_SCHEMA, "lanes": [LEAD, TOOLS, FABLE], "types": ["message", "done", "progress"],
           "approved_tasks": [TASK], "removed_source_refs": [], "max_message_chars": 4096, "max_chunk_chars": 300}
    doc.update(changes)
    return doc


def pinned(doc=None):
    raw = json.dumps(policy_doc() if doc is None else doc, sort_keys=True, separators=(",", ":")).encode("ascii")
    return raw, hashlib.sha256(raw).hexdigest()


def row(message=FINNISH, *, agent=LEAD, minute=0, **changes):
    event = {"ts_utc": "2026-11-07T12:%02d:30.000000Z" % minute, "agent": agent, "agent_uuid": ROSTER[agent],
             "type": "message", "status": "info", "task_id": TASK, "to": FABLE, "message": message,
             "payload": {"note": "synthetic"}}
    event.update(changes)
    return event


def rows_of(*events):
    return {event_id(event): event for event in events}


def build(rows, doc=None):
    raw, pin = pinned(doc)
    return cp.build_content_projection(enabled=True, rows=rows, policy_bytes=raw, policy_sha256=pin, roster=ROSTER)


def texts(projection):
    return [record["stored_text"] for record in projection["records"]]


# --- canaries, assembled at test time --------------------------------------------------------------------------

def _tail(n=24):
    return ("Ab3d" * 12)[:n]


CANARIES = {
    "anthropic_key": lambda: "s" + "k-an" + "t-" + _tail(),
    "openai_key": lambda: "s" + "k-" + _tail(),
    "github_token": lambda: "gh" + "p_" + _tail(),
    "github_pat": lambda: "github" + "_pat_" + _tail(),
    "slack_token": lambda: "xo" + "xb-" + _tail(),
    "aws_key": lambda: "AK" + "IA" + "QWERTYUIOPASDFGH",
    "google_key": lambda: "AI" + "za" + _tail(30),
    "bearer": lambda: "Bea" + "rer " + _tail(),
    "authorization": lambda: "Author" + "ization: " + _tail(),
    "password_assignment": lambda: "pass" + "word=" + "hunter" + "2x",
    "token_assignment": lambda: "to" + "ken: " + "abc" + "123",
    "secret_assignment": lambda: "sec" + "ret = " + "x" + "yz",
    "pem": lambda: "-----BEG" + "IN RSA PRIVATE KEY-----",
    "jwt": lambda: "ey" + "Jhbm9uZSI6dHJ1ZX0." + "eyJzdWIiOiIxIn0." + "c2ln",
    "url_userinfo": lambda: "https" + "://user:" + "pw" + "@example.invalid/x",
    "hex_run": lambda: "0123456789abcdef" * 2,
    "base64_run": lambda: "QmFzZTY0" + "U3ludGhldGljQ2FuYXJ5" + "VmFsdWUx",
}
IDENTIFIERS = {"uuid": lambda: "12345678-" + "1234-4234-8234-" + "123456789abc"}
PERSONAL = {
    "email": lambda: "someone" + chr(64) + "example.invalid",
    "phone": lambda: "+358 40 " + "123 4567",
    "hetu": lambda: "131052" + "-308T",
    "iban": lambda: "FI21" + " 1234 5600 0007 85",
    "iban_lowercase": lambda: "fi21" + "123456" + "00000785",
    "y_tunnus": lambda: "1234567" + "-1",
    "card": lambda: "4111" + "1111" + "1111" + "1111",
}


def _all_canary_values():
    return [make() for group in (CANARIES, IDENTIFIERS, PERSONAL) for make in group.values()]


# --- default off and policy ---------------------------------------------------------------------------------------

def test_default_off_reads_nothing():
    assert cp.build_content_projection() == {"status": "OFF"}
    assert cp.build_content_projection(enabled=False, rows=object()) == {"status": "OFF"}


@pytest.mark.parametrize("enabled", ["true", 1, None])
def test_enabled_must_be_the_real_true(enabled):
    raw, pin = pinned()
    with pytest.raises(cp.ContentRefused):
        cp.build_content_projection(enabled=enabled, rows={}, policy_bytes=raw, policy_sha256=pin, roster=ROSTER)


@pytest.mark.parametrize("change", [
    {"extra": 1}, {"schema": "wd.mind-content-policy.v0"}, {"lanes": [LEAD, RCO1]}, {"lanes": [LEAD, "other-lane"]},
    {"types": ["message", "decision"]}, {"types": ["message", "finding"]}, {"approved_tasks": ["gold-eval-task"]},
    {"approved_tasks": ["feature/branch"]}, {"approved_tasks": ["Bad Task"]}, {"removed_source_refs": ["x" * 64]},
    {"removed_source_refs": ["A" * 64]}, {"max_message_chars": 4097}, {"max_message_chars": True},
    {"max_chunk_chars": 301}, {"max_chunk_chars": 15}, {"lanes": [LEAD, LEAD]}, {"lanes": "codex-lead-1"},
], ids=lambda c: "-".join(c))
def test_policy_is_closed_and_refuses_unsafe_values(change):
    raw, pin = pinned(policy_doc(**change))
    with pytest.raises(cp.ContentRefused):
        cp.load_content_policy(raw, pin)


def test_policy_pin_and_duplicate_keys_refuse():
    raw, pin = pinned()
    with pytest.raises(cp.ContentRefused):
        cp.load_content_policy(raw, "0" * 64)
    duplicate = raw[:-1] + b',"max_chunk_chars":200}'
    with pytest.raises(cp.ContentRefused):
        cp.load_content_policy(duplicate, hashlib.sha256(duplicate).hexdigest())


# --- Finnish text: preserved by a versioned encoding, never transliterated ---------------------------------------

def test_finnish_message_is_one_record_that_decodes_to_the_original_text():
    projection = build(rows_of(row()))
    assert projection["schema"] == cp.SCHEMA and projection["text_encoding"] == cp.TEXT_ENCODING
    assert projection["accepted"] == 1 and projection["refused"] == {}
    (record,) = projection["records"]
    assert TEXT_CLASS.fullmatch(record["stored_text"]) and len(record["stored_text"]) <= 300
    assert cp.decode_text(record["stored_text"]) == FINNISH
    assert "menivat" not in record["stored_text"]  # no silent transliteration
    assert record["summary"] == record["stored_text"]
    assert record["provenance"]["source_kind"] == "bridge_message"
    assert record["provenance"]["source_event_ref"] == event_id(row())
    assert record["provenance"]["canonical_event"] is False
    assert record["meta"]["text_encoding"] == cp.TEXT_ENCODING


def test_combining_and_precomposed_umlauts_give_the_same_record_bytes():
    composed = build(rows_of(row("p\u00e4\u00e4t\u00f6s valmis")))
    combining = build(rows_of(row("pa\u0308a\u0308to\u0308s valmis")))
    assert texts(composed) == texts(combining)


def test_encoding_round_trip_and_strict_decode():
    for text in (FINNISH, "\u00c5\u00c4\u00d6 \u00e5\u00e4\u00f6", "under_score: 1.0 - ok", "(a/b), \"q\" 50%"):
        encoded = cp.encode_text(text)
        assert TEXT_CLASS.fullmatch(encoded) and cp.decode_text(encoded) == text
    for bad in ("_00", "_zzzz", "a_0041", "_005", "x\u00e4", "@"):
        with pytest.raises(cp.ContentRefused):
            cp.decode_text(bad)


def test_long_text_is_chunked_on_word_boundaries_and_rejoins():
    words = ("ty\u00f6vaihe %d valmis" % i for i in range(120))
    text = " ".join(words)
    projection = build(rows_of(row(text)))
    chunks = texts(projection)
    assert len(chunks) > 1 and all(TEXT_CLASS.fullmatch(c) and len(c) <= 300 for c in chunks)
    assert " ".join(cp.decode_text(c) for c in chunks) == text
    counts = {r["provenance"]["chunk_count"] for r in projection["records"]}
    assert counts == {len(chunks)}
    assert [r["provenance"]["chunk_index"] for r in projection["records"]] == list(range(len(chunks)))
    assert len({r["record_id"] for r in projection["records"]}) == len(chunks)


def test_one_overlong_word_is_split_without_breaking_an_escape():
    for text in ("\u00e4" * 200, "x" + "\u00e4" * 200, "xy" + "\u00f6" * 150):
        chunks = texts(build(rows_of(row(text))))
        assert len(chunks) > 1 and all(len(c) <= 300 for c in chunks)
        assert "".join(cp.decode_text(c) for c in chunks) == text


def test_iso_times_and_repo_paths_are_kept_but_local_paths_and_urls_are_tokens():
    text = ("valmis 2026-11-07T17:52:46Z ja 2026-11-07, katso tests/tools/test_x.py; C:" + "\\Users\\someone\\x.txt"
            " ja https" + "://example.invalid/a/b sek\u00e4 /home/someone/x")
    (record,) = build(rows_of(row(text)))["records"]
    decoded = cp.decode_text(record["stored_text"])
    assert "2026-11-07T17:52:46Z" in decoded and "tests/tools/test_x.py" in decoded
    assert "someone" not in decoded and "example.invalid" not in decoded
    assert decoded.count("local-path") == 2 and "url" in decoded


def test_forward_slash_windows_private_path_is_not_retained():
    verdict, encoded = cp.classify("see C:/Users/Example/private.txt")
    assert verdict == "KEEP"
    assert cp.decode_text(encoded) == "see local-path"


def test_synthetic_grouped_license_key_is_refused():
    assert cp.classify("key 36-ABCD-EFGH-JKLM-NPQR-STUV-WXYZ234") == ("DROP", "credential")


@pytest.mark.parametrize("message", [
    "APP_PASSWORD=synthetic-value",
    "salasana: synthetic-value",
    "tunnussana is synthetic-value",
    "PIN-koodi: synthetic-value",
    "password is synthetic-value",
    "synthetic AbCdEfGhIjKlMnOpQrStUv_WxYz1234567890abc",
    "synthetic ABCDE-FGHIJ-KLMNO-PQRST-UVWXY",
])
def test_additional_synthetic_credentials_are_refused(message):
    assert cp.classify(message) == ("DROP", "credential")


@pytest.mark.parametrize("path", [
    r"C:\Users\Jane Smith\private.txt",
    "C:/Users/Jane Smith/private.txt",
])
def test_windows_path_with_spaces_is_not_retained(path):
    verdict, encoded = cp.classify("see " + path)
    assert verdict != "KEEP" or "Smith" not in cp.decode_text(encoded)


# --- fail-closed screening before chunking -------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(CANARIES))
def test_credential_canary_drops_the_whole_row(name, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    value = CANARIES[name]()
    projection = build(rows_of(row("valmis " + value + " ja muuta"), row("muu viesti", minute=1)))
    assert projection["refused"] == {"credential": 1} and projection["accepted"] == 1
    assert texts(projection) == [cp.encode_text("muu viesti")]
    _assert_never_visible(value, projection, capsys, caplog)


@pytest.mark.parametrize("group, name", [("identifier", n) for n in IDENTIFIERS] +
                         [("personal", n) for n in PERSONAL])
def test_identifier_and_personal_canaries_drop_the_row(group, name, capsys, caplog):
    value = (IDENTIFIERS if group == "identifier" else PERSONAL)[name]()
    projection = build(rows_of(row("tieto " + value + " loppu")))
    assert projection["refused"] == {group: 1} and projection["records"] == []
    _assert_never_visible(value, projection, capsys, caplog)


@pytest.mark.parametrize("marker", ["sealed", "GOLD", "held" + "out", "Judge"])
def test_sealed_marker_anywhere_in_text_drops_the_row(marker):
    projection = build(rows_of(row("tulos " + marker + "-joukosta")))
    assert projection["refused"] == {"sealed": 1} and projection["records"] == []


@pytest.mark.parametrize("marker", ["priv" + "ate_mar" + "ker", "_do" + "_not_le" + "ak"])
def test_workspace_guarded_word_drops_the_row_instead_of_failing_the_build(marker):
    projection = build(rows_of(row("ennen " + marker + " jalkeen"), row("muu", minute=1)))
    assert projection["refused"] == {"sealed": 1} and projection["accepted"] == 1


def test_fullwidth_and_zero_width_split_credentials_are_still_caught():
    value = CANARIES["github_token"]()
    fullwidth = "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in value)
    split = value[:4] + "\u200b" + value[4:]
    bidi = value[:3] + "\u202e" + value[3:]
    projection = build(rows_of(row("a " + fullwidth), row("b " + split, minute=1), row("c " + bidi, minute=2)))
    assert projection["refused"] == {"credential": 3} and projection["records"] == []


def test_credential_across_a_chunk_boundary_drops_the_whole_row():
    value = CANARIES["password_assignment"]()
    text = "sana " * 59 + value + " loppu " * 30
    projection = build(rows_of(row(text)))
    assert projection["refused"] == {"credential": 1} and projection["records"] == []


def test_phone_next_to_a_date_is_still_caught():
    projection = build(rows_of(row("2026-11-07 soita " + PERSONAL["phone"]())))
    assert projection["refused"] == {"personal": 1}


@pytest.mark.parametrize("text, token", [
    ("vir\u0435 sana", "mixed_script"),             # Cyrillic e inside a Latin word
    ("\u0432\u0441\u0435 \u0445\u043e\u0440\u043e\u0448\u043e", "encoding"),  # all Cyrillic
    ("emoji \U0001F600 here", "encoding"),
    ("ctl \x07 bell", "encoding"),
    ("   \u200b  ", "empty"),
    ("x" * 4097, "oversize"),
])
def test_unsupported_or_empty_text_drops_with_its_token(text, token):
    projection = build(rows_of(row(text)))
    assert projection["refused"] == {token: 1} and projection["records"] == []


def test_raw_input_limit_applies_before_format_characters_are_removed():
    padded = "x" * 4000 + "\u200b" * 97
    assert cp.classify(padded) == ("DROP", "oversize")
    assert cp.classify("x" * 4000 + "\u200b" * 96)[0] == "KEEP"


def test_iban_detector_checks_the_mod_97_sum():
    assert cp._iban("tili FI21" + "12345600000785 nyt") and cp._iban("gb82" + "west12345698765432")
    assert not cp._iban("tili FI21" + "12345600000786 nyt") and not cp._iban("sha256 a1ebefa3 ok")


def test_classify_drops_invalid_text_directly():
    # A lone surrogate cannot reach a row: the event ref of such a row cannot be computed.
    assert cp.classify("bad \ud800 surrogate") == ("DROP", "encoding")
    assert cp.classify(None) == ("DROP", "encoding")
    assert cp.classify(b"bytes") == ("DROP", "encoding")
    assert cp.classify("ok text") == ("KEEP", "ok text")


def test_a_detector_error_drops_as_unknown_without_echo(monkeypatch, capsys, caplog):
    value = CANARIES["openai_key"]()

    def broken(_text):
        raise RuntimeError("synthetic detector failure")

    monkeypatch.setattr(cp, "_screen", broken)
    projection = build(rows_of(row("ok " + value)))
    assert projection["refused"] == {"unknown": 1} and projection["records"] == []
    _assert_never_visible(value, projection, capsys, caplog)


def test_every_canary_class_together_leaves_no_trace(capsys, caplog):
    values = _all_canary_values()
    events = [row("viesti " + v, minute=i) for i, v in enumerate(values)]
    projection = build(rows_of(*events))
    assert projection["records"] == [] and sum(projection["refused"].values()) == len(values)
    for value in values:
        _assert_never_visible(value, projection, capsys, caplog)


def _assert_never_visible(value, projection, capsys, caplog):
    blob = json.dumps(projection, ensure_ascii=False)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    seen = capsys.readouterr()
    for text in (blob, seen.out, seen.err, caplog.text):
        assert value not in text and digest not in text
    assert cp.encode_text(value) not in blob if TEXT_CLASS.fullmatch(cp.encode_text(value)) else True


# --- source boundaries ---------------------------------------------------------------------------------------------

def test_payload_paths_and_identities_are_never_read():
    value = CANARIES["github_token"]()
    event = row("tavallinen viesti", payload={"note": value}, paths=[value], write_scope=[value], cwd=value,
                run_id=value)
    projection = build(rows_of(event))
    assert projection["accepted"] == 1 and value not in json.dumps(projection)


@pytest.mark.parametrize("changes", [
    {"agent": RCO1}, {"agent": RCO2}, {"type": "decision"}, {"type": "finding"}, {"status": "veto"},
    {"status": "changes_requested"}, {"to": RCO2}, {"payload": {"to": RCO1}}, {"task_id": "feature/x"},
], ids=lambda c: "-".join("%s_%s" % (k, v if isinstance(v, str) else "obj") for k, v in c.items()))
def test_review_signal_rows_are_excluded_silently(changes):
    agent = changes.pop("agent", LEAD)
    projection = build(rows_of(row(agent=agent, **changes)))
    assert projection["records"] == [] and projection["refused"] == {}


@pytest.mark.parametrize("changes, token", [
    ({"task_id": "other-task"}, "task_not_approved"), ({"type": "claim"}, "type_not_allowed"),
    ({"agent_uuid": ROSTER[TOOLS]}, "agent_identity_mismatch"), ({"message": None}, "no_message"),
    ({"message": ""}, "no_message"), ({"ts_utc": "yesterday"}, "time_invalid"),
], ids=["task", "type", "identity", "none", "blank", "time"])
def test_ineligible_rows_are_counted_by_token(changes, token):
    projection = build(rows_of(row(**changes)))
    assert projection["records"] == [] and projection["refused"] == {token: 1}


def test_lane_outside_the_policy_is_counted():
    projection = build(rows_of(row(agent=TOOLS)), policy_doc(lanes=[LEAD]))
    assert projection["records"] == [] and projection["refused"] == {"lane_not_allowed": 1}


def test_row_key_must_be_the_event_ref_and_rows_must_be_a_mapping():
    event = row()
    with pytest.raises(cp.ContentRefused):
        build({"0" * 64: event})
    with pytest.raises(cp.ContentRefused):
        build([event])


# --- removal and validation -----------------------------------------------------------------------------------------

def test_removed_source_ref_disappears_from_the_next_generation():
    keep, gone = row("pysyy", minute=1), row("poistuu " * 80, minute=2)
    rows = rows_of(keep, gone)
    first = build(rows)
    assert {r["provenance"]["source_event_ref"] for r in first["records"]} == {event_id(keep), event_id(gone)}
    second = build(rows, policy_doc(removed_source_refs=[event_id(gone)]))
    assert {r["provenance"]["source_event_ref"] for r in second["records"]} == {event_id(keep)}
    assert second["refused"] == {"removed": 1}
    raw, pin = pinned(policy_doc(removed_source_refs=[event_id(gone)]))
    with pytest.raises(cp.ContentRefused):
        cp.validate_content_projection(first, rows=rows, policy_bytes=raw, policy_sha256=pin, roster=ROSTER)


def test_validator_accepts_only_the_exact_re_derivation():
    rows = rows_of(row(), row("toinen", minute=1))
    raw, pin = pinned()
    projection = build(rows)
    assert cp.validate_content_projection(projection, rows=rows, policy_bytes=raw, policy_sha256=pin,
                                          roster=ROSTER) is None
    edited = json.loads(json.dumps(projection))
    edited["records"][0]["stored_text"] = cp.encode_text("muokattu")
    extra = json.loads(json.dumps(projection))
    extra["note"] = "x"
    for bad in (edited, extra, {"schema": cp.SCHEMA}, None):
        with pytest.raises(cp.ContentRefused):
            cp.validate_content_projection(bad, rows=rows, policy_bytes=raw, policy_sha256=pin, roster=ROSTER)


def test_projection_is_deterministic_and_bounded_for_the_lane_output():
    rows = rows_of(row(), row("toinen viesti", minute=3), row("kolmas", agent=FABLE, minute=2))
    first, second = build(rows), build(dict(reversed(list(rows.items()))))
    assert first == second
    for record in first["records"]:
        text = record["stored_text"]
        assert TEXT_CLASS.fullmatch(text) and not HEX_RUN.search(text) and len(text.encode("ascii")) <= 320
        assert record["available_utc"] > record["time_utc"]
        assert record["provenance"]["available_utc"] == record["available_utc"]
    assert first["review_signal_excluded"] is True and first["authority"] == "none"


def test_the_personal_detectors_are_exact_copies_of_the_redactor_detectors():
    from waggledance.core.bridge_llm import redactor
    for name in ("EMAIL_RE", "CREDIT_CARD_RE", "HETU_RE", "Y_TUNNUS_RE", "PHONE_RE"):
        copy, origin = getattr(cp, "_" + name), getattr(redactor, name)
        assert (copy.pattern, copy.flags) == (origin.pattern, origin.flags), name
    assert cp._PERSONAL == (cp._EMAIL_RE, cp._CREDIT_CARD_RE, cp._HETU_RE, cp._Y_TUNNUS_RE)
