"""Synthetic tests for the default-off useful content memory (increment 3: builder entry and pinned ask)."""
import hashlib
import json

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_index_builder as sib
from sandbox.mind.step2 import test_shared_content_projection as tcp
from sandbox.mind.step2 import test_shared_doc_projection as tdp

np = pytest.importorskip("numpy")

IDENTITY = {"model": "synthetic-bow", "revision": "a" * 40, "weights_sha256": "b" * 64}
DIM = 64
ASK_TIME = "2026-11-08T00:00:00Z"
A = chr(0xE4)
O = chr(0xF6)
PUNCT = ".,:;!?()[]#*"


def words(text):
    out = []
    for raw in text.lower().split():
        word = raw.strip(PUNCT)
        if len(word) > 1:
            out.append(word)
    return out


def bow(text):
    vector = [0.0] * DIM
    for word in words(text):
        vector[int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16) % DIM] += 1.0
    norm = sum(x * x for x in vector) ** 0.5 or 1.0
    return [x / norm for x in vector]


class Spy:
    def __init__(self):
        self.seen = []

    def __call__(self, texts):
        self.seen.extend(texts)
        return np.array([bow(text) for text in texts], dtype=np.float32)


class Encoder:
    def query(self, text):
        return np.array(bow(text), dtype=np.float32)


def message_source(*events):
    raw, pin = tcp.pinned()
    return {"rows": tcp.rows_of(*events), "policy_bytes": raw, "policy_sha256": pin, "roster": tcp.ROSTER}


def doc_source(tmp_path, files, paths):
    root, commit = tdp.make_repo(tmp_path, files)
    raw, pin = tdp.policy(commit, paths)
    return {"repo_root": str(root), "policy_bytes": raw, "policy_sha256": pin}, root, commit


DOC_TEXT = "Hex-muisti tallentaa parven p" + A + "" + A + "t" + O + "kset ja niiden perustelut."
MSG_TEXT = tcp.FINNISH


@pytest.fixture
def sources(tmp_path):
    messages = message_source(tcp.row(MSG_TEXT, minute=1), tcp.row("Lane kuittasi tehtavan valmiiksi.", minute=2,
                                                                     type="done", agent=tcp.TOOLS))
    documents, root, commit = doc_source(tmp_path, {"docs/guide.md": tdp.doc_bytes(DOC_TEXT)}, ["docs/guide.md"])
    return messages, documents, commit


def build(tmp_path, messages=None, documents=None, encode=None, name="content-a"):
    pytest.importorskip("faiss")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return cm.build_content_memory(enabled=True, messages=messages, documents=documents,
                                   encode=encode or Spy(), identity=IDENTITY, dim=DIM, root=out, name=name)


# --- default off -----------------------------------------------------------------------------------------------------

def test_default_is_off_and_touches_nothing(tmp_path):
    assert cm.build_content_memory() == {"status": "OFF"}
    assert cm.ask_content_memory(tmp_path / "absent", "0" * 64, None, "q", ASK_TIME) == {"status": "OFF"}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", [1, "true", None, 0])
def test_enable_flags_must_be_exactly_true(tmp_path, value):
    with pytest.raises(cm.ContentMemoryRefused, match="enabled_invalid"):
        cm.build_content_memory(enabled=value)
    with pytest.raises(cm.ContentMemoryRefused, match="enabled_invalid"):
        cm.ask_content_memory(tmp_path, "0" * 64, None, "q", ASK_TIME, enabled=value)


# --- build ---------------------------------------------------------------------------------------------------------

def test_memory_holds_both_sources_under_its_own_scope(tmp_path, sources):
    messages, documents, commit = sources
    built = build(tmp_path, messages, documents)
    memory_dir = tmp_path / "out" / "content-a"
    raw = (memory_dir / "memory.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == built["memory_sha256"]
    manifest = json.loads(raw)
    assert manifest["scope"] == cm.SCOPE != sib.SCOPE and manifest["projection_schema"] == cm.SCHEMA
    assert manifest["placement"] == cm.PLACEMENT_RULE and manifest["records"] == built["records"] == 4
    assert built["descriptor"]["schema"] == cm.DESCRIPTOR_SCHEMA != sib.DESCRIPTOR_SCHEMA
    assert built["descriptor"]["memory_sha256"] == built["memory_sha256"]
    descriptor = (memory_dir / "descriptor.json").read_bytes()
    assert hashlib.sha256(descriptor).hexdigest() == built["descriptor_sha256"]
    projection = json.loads((memory_dir / "projection.json").read_bytes())
    assert projection["schema"] == cm.SCHEMA and projection["messages"]["accepted"] == 2
    assert projection["documents"]["accepted"] == 2 and projection["documents"]["commit"] == commit
    assert set(manifest["cells"]) == {"general", "system", "learning"}


def test_vectors_are_computed_from_decoded_text_not_escapes(tmp_path, sources):
    messages, documents, _ = sources
    spy = Spy()
    build(tmp_path, messages, documents, encode=spy)
    assert MSG_TEXT in spy.seen and DOC_TEXT in spy.seen
    assert not any("_00e4" in text for text in spy.seen)


def test_messages_only_and_documents_only_both_build(tmp_path, sources):
    messages, documents, _ = sources
    assert build(tmp_path, messages=messages, name="m")["records"] == 2
    assert build(tmp_path, documents=documents, name="d")["records"] == 2


@pytest.mark.parametrize("change, token", [
    ({}, "sources_missing"),
    ({"messages": {"rows": {}}}, "sources_invalid"),
    ({"documents": {"repo_root": "x", "policy_bytes": b"", "policy_sha256": "0" * 64, "extra": 1}}, "sources_invalid"),
    ({"messages": []}, "sources_invalid"),
])
def test_sources_are_closed(tmp_path, change, token):
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(cm.ContentMemoryRefused, match=token):
        cm.build_content_memory(enabled=True, encode=Spy(), identity=IDENTITY, dim=DIM, root=out, name="x",
                                **change)
    assert list(out.iterdir()) == []


def test_projection_refusals_pass_through_as_closed_tokens(tmp_path, sources):
    messages, documents, _ = sources
    bad = dict(messages, policy_sha256="0" * 64)
    with pytest.raises(cm.ContentMemoryRefused, match="policy_refused"):
        build(tmp_path, messages=bad)
    worse = dict(documents, repo_root=str(tmp_path / "nowhere"))
    with pytest.raises(cm.ContentMemoryRefused, match="repo_refused"):
        build(tmp_path, documents=worse)


def test_empty_projection_refuses(tmp_path):
    messages = message_source(tcp.row("x", agent=tcp.RCO1))
    with pytest.raises(cm.ContentMemoryRefused, match="projection_empty"):
        build(tmp_path, messages=messages)


def test_existing_memory_is_never_replaced(tmp_path, sources):
    messages, documents, _ = sources
    build(tmp_path, messages, documents)
    with pytest.raises(cm.ContentMemoryRefused, match="memory_exists"):
        build(tmp_path, messages, documents)


# --- ask -----------------------------------------------------------------------------------------------------------

def ask(tmp_path, built, situation, when=ASK_TIME):
    return cm.ask_content_memory(tmp_path / "out" / "content-a", built["memory_sha256"], Encoder(), situation, when,
                                 enabled=True)


def test_ask_recalls_a_finnish_message_by_meaning_with_its_source(tmp_path, sources):
    messages, documents, _ = sources
    built = build(tmp_path, messages, documents)
    answer = ask(tmp_path, built, "onnistuiko yhdist" + A + "minen ja meniv" + A + "tk" + O + " testit")
    top = answer["results"][0]
    assert top["source_kind"] == "bridge_message" and cp.decode_text(top["text"]) == MSG_TEXT
    assert top["text_encoding"] == cp.TEXT_ENCODING and tcp.TEXT_CLASS.fullmatch(top["text"])
    source_refs = set(messages["rows"])
    assert top["source_ref_sha256"] in source_refs and top["task_id"] == tcp.TASK and top["agent"] == tcp.LEAD
    assert answer["coverage"]["cutoff_utc"] == json.loads(
        (tmp_path / "out" / "content-a" / "memory.json").read_bytes())["cutoff_utc"]


def test_ask_recalls_a_document_with_commit_path_and_blob(tmp_path, sources):
    messages, documents, commit = sources
    built = build(tmp_path, messages, documents)
    answer = ask(tmp_path, built, "miten hex-muisti tallentaa parven p" + A + A + "t" + O + "kset")
    top = answer["results"][0]
    assert top["source_kind"] == "repo_doc" and cp.decode_text(top["text"]) == DOC_TEXT
    assert top["commit"] == commit and top["path"] == "docs/guide.md" and len(top["blob_id"]) == 40
    assert top["cell"] == "learning"


def test_ask_refuses_wrong_pin_and_tampered_files(tmp_path, sources):
    messages, documents, _ = sources
    built = build(tmp_path, messages, documents)
    memory_dir = tmp_path / "out" / "content-a"
    with pytest.raises(cm.ContentMemoryRefused, match="memory_mismatch"):
        cm.ask_content_memory(memory_dir, "0" * 64, Encoder(), "q", ASK_TIME, enabled=True)
    projection = memory_dir / "projection.json"
    projection.write_bytes(projection.read_bytes().replace(b'"accepted": 2', b'"accepted": 9', 1))
    with pytest.raises(cm.ContentMemoryRefused, match="projection_mismatch"):
        ask(tmp_path, built, "q")


def test_ask_refuses_a_memory_of_another_scope(tmp_path, sources):
    messages, documents, _ = sources
    built = build(tmp_path, messages, documents)
    memory_dir = tmp_path / "out" / "content-a"
    manifest = json.loads((memory_dir / "memory.json").read_bytes())
    manifest["scope"] = sib.SCOPE
    raw = json.dumps(manifest).encode("utf-8")
    (memory_dir / "memory.json").write_bytes(raw)
    with pytest.raises(cm.ContentMemoryRefused, match="memory_invalid"):
        cm.ask_content_memory(memory_dir, hashlib.sha256(raw).hexdigest(), Encoder(), "q", ASK_TIME, enabled=True)
    assert built["memory_sha256"] != hashlib.sha256(raw).hexdigest()


def test_nothing_is_visible_before_its_time(tmp_path, sources):
    messages, documents, _ = sources
    built = build(tmp_path, messages, documents)
    answer = ask(tmp_path, built, "hex-muisti tallentaa", when="2026-10-01T00:00:00Z")
    assert answer["results"] == [] and answer["coverage"]["cutoff_utc"]


def test_ask_time_must_parse(tmp_path, sources):
    messages, documents, _ = sources
    built = build(tmp_path, messages, documents)
    with pytest.raises(cm.ContentMemoryRefused, match="situation_time_invalid"):
        ask(tmp_path, built, "q", when="not-a-time")


def repin(memory_dir, mutate_projection=None, mutate_manifest=None):
    """Rewrite projection.json and memory.json with a change and a consistent pin chain; return the new memory pin."""
    from sandbox.mind.step2 import cell as cell_mod
    projection = json.loads((memory_dir / "projection.json").read_bytes())
    if mutate_projection is not None:
        mutate_projection(projection)
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    (memory_dir / "projection.json").write_bytes(projection_bytes)
    manifest = json.loads((memory_dir / "memory.json").read_bytes())
    manifest["projection_sha256"] = hashlib.sha256(projection_bytes).hexdigest()
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    raw = cell_mod.canonical(manifest).encode("utf-8")
    (memory_dir / "memory.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def ask_pinned(memory_dir, pin, situation):
    return cm.ask_content_memory(memory_dir, pin, Encoder(), situation, ASK_TIME, enabled=True)


def test_repin_helper_keeps_an_unchanged_memory_answering(tmp_path, sources):
    messages, documents, _ = sources
    build(tmp_path, messages, documents)
    memory_dir = tmp_path / "out" / "content-a"
    pin = repin(memory_dir)
    query = "miten hex-muisti tallentaa parven p" + A + A + "t" + O + "kset"
    assert ask_pinned(memory_dir, pin, query)["results"]


def test_ask_refuses_a_manifest_of_another_projection_schema(tmp_path, sources):
    messages, documents, _ = sources
    build(tmp_path, messages, documents)
    memory_dir = tmp_path / "out" / "content-a"
    pin = repin(memory_dir, mutate_manifest=lambda manifest: manifest.update(projection_schema=cp.SCHEMA))
    with pytest.raises(cm.ContentMemoryRefused, match="memory_invalid"):
        ask_pinned(memory_dir, pin, "q")


def test_ask_refuses_a_part_of_another_schema(tmp_path, sources):
    messages, documents, _ = sources
    build(tmp_path, messages, documents)
    memory_dir = tmp_path / "out" / "content-a"
    pin = repin(memory_dir, lambda projection: projection["documents"].update(schema=cp.SCHEMA))
    with pytest.raises(cm.ContentMemoryRefused, match="projection_invalid"):
        ask_pinned(memory_dir, pin, "q")


def test_a_hit_whose_record_id_differs_in_the_projection_is_unresolved(tmp_path, sources):
    messages, documents, _ = sources
    build(tmp_path, messages, documents)
    memory_dir = tmp_path / "out" / "content-a"

    def rename(projection):
        for record in projection["documents"]["records"]:
            record["record_id"] = "doc:" + "0" * 64
    pin = repin(memory_dir, rename)
    with pytest.raises(cm.ContentMemoryRefused, match="result_unresolved"):
        ask_pinned(memory_dir, pin, "miten hex-muisti tallentaa parven p" + A + A + "t" + O + "kset")


def test_document_hits_cite_the_document_source_ref(tmp_path, sources):
    messages, documents, commit = sources
    built = build(tmp_path, messages, documents)
    top = ask(tmp_path, built, "miten hex-muisti tallentaa parven p" + A + A + "t" + O + "kset")["results"][0]
    expected = hashlib.sha256(cp._canonical({"blob": top["blob_id"], "commit": commit,
                                             "path": "docs/guide.md"})).hexdigest()
    assert top["source_ref_sha256"] == expected != top["ref"]
    projection = json.loads((tmp_path / "out" / "content-a" / "projection.json").read_bytes())
    labels = {record["provenance"]["ref_kind"] for record in projection["documents"]["records"]}
    assert labels == {"sha256_of_doc_chunk_and_source"}
