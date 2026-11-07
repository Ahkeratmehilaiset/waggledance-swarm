"""RC2: fabricated real-format cards; no live data or native model runtime."""
import base64
import copy
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import sysconfig
import dataclasses
from dataclasses import replace
from pathlib import Path

import pytest

from sandbox.mind.step2 import runtime_launcher
from sandbox.mind.step2 import wd_q4_private_read as reader
from tools import wd_q4_agent_query as q4
from tools import wd_q4_public_query as public
from tools import wd_q4_public_pointer_resolver as pointers
from tools import wd_q4_resolved_child as child
from tools import wd_q4_public_child as public_child
from tests.tools.test_wd_q4_real_card_snapshot_e2e import real_snapshot, real_fixture, rt


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def route(real_snapshot, monkeypatch):
    directory, built, _, snapshot, projection, args = real_snapshot
    calls = []
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kwargs: rt.fx.Encoder())
    monkeypatch.setattr(reader, "bind_runtime_encoder", lambda *a, **kw: None)
    monkeypatch.setattr(q4, "_utc_now", lambda: dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc))

    def launcher(*, query, **kwargs):
        calls.append(True)
        question = json.loads(query)
        out = reader._private_read_locked(directory, directory / "descriptor.json", built["descriptor_sha256"],
            question["situation"], question["time"], r"C:\fake\synthetic-model", rt.fx.IDENTITY["revision"])
        return runtime_launcher._validate_output(json.dumps(out).encode(), 0, built["descriptor_sha256"])

    monkeypatch.setattr(q4, "_invoke_launcher", launcher)
    memory = (directory / "memory.json").read_bytes()
    data = dict(memory_manifest_bytes=memory, projection_bytes=(directory / "projection.json").read_bytes(),
        expected_memory_sha256=built["memory_sha256"], card_decision_bytes=args["decision_bytes"],
        card_decision_sha256=args["decision_sha256"],
        **{k: args[k] for k in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256")})
    return dict(directory=directory, built=built, snapshot=snapshot, args=args, data=data, projection=projection,
                config=rt.config(directory, built["descriptor_sha256"]), calls=calls)


def question():
    return io.BytesIO(json.dumps(dict(situation="merged green pr 2046 genesis", time="2026-11-02T00:00:00Z")).encode())


def break_source(route):
    path = Path(route["args"]["source_path"])
    path.write_bytes(b"!" + path.read_bytes()[1:])


def test_resolved_child_refuses_real_without_c2_source_tuple(route):
    break_source(route)
    data = route["data"].copy()
    memory = data.pop("memory_manifest_bytes")
    memory_sha = data.pop("expected_memory_sha256")
    projection = data.pop("projection_bytes")
    stdin, stdout = question(), io.StringIO()
    code = child.run_resolved(route["config"], stdin, stdout, memory_bytes=memory, memory_sha256=memory_sha,
        projection_bytes=projection, projection_sha256=sha(projection), **data)
    assert code == 2 and json.loads(stdout.getvalue())["status"] == "REFUSED"
    assert route["calls"] == [] and stdin.tell() == 0


def public_case(route, monkeypatch):
    directory = route["directory"]
    root = directory.parent
    pointer = b'{"synthetic-test-public-pointer":true}'
    (root / pointers.checkpoint.POINTER).write_bytes(pointer)
    # The existing legacy fixture substitutes its v6 history view. It is
    # deliberately not a C2 proof and must never authorize a real-card query.
    view = dict(pointer_sha256=sha(pointer), generation=directory.name,
        artifacts=dict(descriptor_sha256=route["built"]["descriptor_sha256"],
                       memory_sha256=route["built"]["memory_sha256"],
                       projection_sha256=sha(route["data"]["projection_bytes"]), receipt_sha256="7" * 64),
        anchor_sha256=json.loads(route["args"]["decision_bytes"])["source_prefix"]["sha256"],
        cursor_end_sha256="9" * 64, watermark_sha256="a" * 64)
    monkeypatch.setattr(pointers.checkpoint, "read_verified_pointer", lambda *a, **kw: copy.deepcopy(view))
    policy = json.dumps(dict(schema="wd.mind-public-status-policy.v1",
                             public_tasks=["mind-public-knowledge-v1"], public_types=["status"]),
                        sort_keys=True, separators=(",", ":")).encode()
    descriptor = json.loads((directory / "descriptor.json").read_bytes())
    return dict(root=root, enabled=True,
            expected_pointer_sha256=sha(pointer), expected_source_sha256=view["anchor_sha256"],
            expected_encoder_sha256=public._encoder_digest(descriptor["encoder"]),
            public_policy_bytes=policy, public_policy_sha256=sha(policy),
            **{k:v for k,v in route["data"].items() if k in (
                "policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
                "card_decision_bytes", "card_decision_sha256")})


def test_legacy_public_refuses_real_without_c2_source_tuple(route, monkeypatch):
    break_source(route)
    with pytest.raises(public.PublicQueryRefused):
        public.query_public(route["config"], question(), **public_case(route, monkeypatch))
    assert route["calls"] == []


def test_public_child_legacy_real_refuses_before_stdin(route, monkeypatch):
    break_source(route)
    stdin, stdout = question(), io.StringIO()
    code = public_child.run_public(route["config"], stdin, stdout, **public_case(route, monkeypatch))
    assert code == 2 and json.loads(stdout.getvalue())["status"] == "REFUSED"
    assert stdin.tell() == 0 and route["calls"] == []


def test_public_pointer_legacy_real_refuses_without_c2(route, monkeypatch):
    args = public_case(route, monkeypatch)
    del args["expected_source_sha256"], args["expected_encoder_sha256"]
    with pytest.raises(pointers.PublicPointerRefused):
        pointers.resolve_public_pointer(**args)


def test_direct_query_helper_real_without_c2_is_a_sibling_boundary(route):
    break_source(route)
    tasks, _ = pointers.adapter.strict_pins(route["data"]["policy_bytes"], route["data"]["policy_sha256"],
                                            route["data"]["roster_bytes"], route["data"]["roster_sha256"])
    with pytest.raises(q4.QueryRefused):
        q4.query_and_resolve(route["config"], question(), approved_tasks=tasks, **route["data"])
    assert route["calls"] == []


def direct_query(route, snapshot):
    tasks, _ = pointers.adapter.strict_pins(route["data"]["policy_bytes"], route["data"]["policy_sha256"],
                                            route["data"]["roster_bytes"], route["data"]["roster_sha256"])
    return q4.query_and_resolve(route["config"], question(), approved_tasks=tasks,
                                card_snapshot=snapshot, **route["data"])


def actual_tuple(route):
    return dict(route["snapshot"], generation_dir=route["directory"])


def test_direct_query_actual_c2_tuple_answers(route):
    assert direct_query(route, actual_tuple(route))["status"] == "ANSWER"


def test_direct_query_projection_changed_after_first_c2_refuses(route, monkeypatch):
    launch = q4._invoke_launcher
    def mutate(**kwargs):
        result = launch(**kwargs)
        path = route["directory"] / "projection.json"
        path.write_bytes(path.read_bytes() + b"tamper")
        return result
    monkeypatch.setattr(q4, "_invoke_launcher", mutate)
    with pytest.raises(q4.QueryRefused):
        direct_query(route, actual_tuple(route))
    assert route["calls"] == [True]


@pytest.mark.parametrize("kind", ["memory_bytes", "projection_bytes", "decision_pin", "descriptor_pin", "memory_path"])
def test_direct_query_tuple_mismatch_refuses(route, kind):
    snapshot = actual_tuple(route)
    if kind == "memory_bytes":
        route["data"]["memory_manifest_bytes"] += b" "
        route["data"]["expected_memory_sha256"] = sha(route["data"]["memory_manifest_bytes"])
    elif kind == "projection_bytes":
        route["data"]["projection_bytes"] += b" "
    elif kind == "decision_pin":
        snapshot["decision_sha256"] = "0" * 64
    elif kind == "descriptor_pin":
        route["config"] = replace(route["config"], descriptor_sha256="0" * 64)
    else:
        route["config"] = replace(route["config"], memory=r"C:\synthetic\other")
    with pytest.raises(q4.QueryRefused):
        direct_query(route, snapshot)
    assert route["calls"] == []


def staged_args(route):
    args = route["snapshot"].copy()
    args["root"] = route["directory"].parent
    args["card_decision_bytes"] = args.pop("decision_bytes")
    args["card_decision_sha256"] = args.pop("decision_sha256")
    args["expected_source_sha256"] = json.loads(args["card_decision_bytes"])["source_prefix"]["sha256"]
    args["expected_encoder_sha256"] = public._encoder_digest(
        json.loads((route["directory"] / "descriptor.json").read_bytes())["encoder"])
    return dict(args, enabled=True)


def test_staged_query_actual_c2_valid_real_still_answers(route):
    answer = public.query_staged_card_snapshot(route["config"], question(), **staged_args(route))
    assert answer["status"] == "ANSWER" and route["calls"] == [True]


def test_staged_query_actual_c2_rejects_changed_source_before_query(route):
    break_source(route)
    with pytest.raises(public.PublicQueryRefused):
        public.query_staged_card_snapshot(route["config"], question(), **staged_args(route))
    assert route["calls"] == []


_STAGED_PROCESS = r'''
import base64,datetime,hashlib,io,json,os,re,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2]);sys.path.insert(0,sys.argv[1])
import numpy as np
from sandbox.mind.step2 import runtime_launcher,wd_q4_private_read as reader,shared_card_checkpoint as c2
from waggledance.core.bridge_log_reader import BridgeCursor
from tools import wd_q4_agent_query as q4,wd_q4_public_child as child
wire=json.load(sys.stdin)
for relative,pin in wire['source_pins'].items():
 assert hashlib.sha256((Path(sys.argv[1])/relative).read_bytes()).hexdigest()==pin
class Encoder:
 def query(self,text):
  vector=[0.0]*64
  for token in re.findall(r'[a-z0-9]{2,}',text.lower()):
   vector[int(hashlib.sha256(token.encode()).hexdigest(),16)%64]+=1.0
  norm=sum(x*x for x in vector)**0.5 or 1.0
  return np.array([x/norm for x in vector],dtype=np.float32)
reader.ENCODER_FACTORY=lambda **kw:Encoder()
reader.bind_runtime_encoder=lambda *a,**kw:None
q4._utc_now=lambda:datetime.datetime(2026,11,3,tzinfo=datetime.timezone.utc)
config=q4.VerifiedQ4Config(**wire['config']);events=[]
actual_c2=c2.validate_card_snapshot
def observed_c2(**kwargs):
 value=actual_c2(**kwargs);events.append('c2:'+value['status']);return value
c2.validate_card_snapshot=observed_c2
def launcher(*,query,**kwargs):
 events.append('in_process_private_reader')
 question=json.loads(query)
 result=reader._private_read_locked(config.memory,config.descriptor,config.descriptor_sha256,
     question['situation'],question['time'],r'C:\fake\synthetic-model',config.runtime_revision)
 return runtime_launcher._validate_output(json.dumps(result).encode(),0,config.descriptor_sha256)
q4._invoke_launcher=launcher
args=wire['arguments']
for key in wire['byte_fields']:args[key]=base64.b64decode(args[key],validate=True)
args['end_cursor']=BridgeCursor(**args['end_cursor'])
out=io.StringIO()
code=child.run_staged_card(config,io.BytesIO(base64.b64decode(wire['query'],validate=True)),out,**args)
print(json.dumps(dict(pid=os.getpid(),code=code,events=events,result=json.loads(out.getvalue()))))
'''


@pytest.mark.skipif(os.name != "nt", reason="Windows staged-child process witness")
def test_actual_staged_child_process_keeps_real_c2_checks(route):
    candidate = Path(os.environ["WD_Q4_RC2_CANDIDATE_ROOT"])
    arguments = staged_args(route)
    byte_fields = [k for k,v in arguments.items() if type(v) is bytes]
    wire_args = {k:base64.b64encode(v).decode() if k in byte_fields else v for k,v in arguments.items()}
    wire_args["root"] = str(wire_args["root"])
    wire_args["end_cursor"] = dataclasses.asdict(arguments["end_cursor"])
    sources = ("tools/wd_q4_agent_query.py", "tools/wd_q4_public_query.py", "tools/wd_q4_public_child.py")
    repository = Path(__file__).resolve().parents[2]
    wire = dict(arguments=wire_args, byte_fields=byte_fields, config=dataclasses.asdict(route["config"]),
                query=base64.b64encode(question().read()).decode(),
                source_pins={p:sha((repository/p).read_bytes()) for p in sources})
    python = Path(sys.executable)
    assert sha(python.read_bytes()) == "d932e5e2f324d57f392e8fd063dcf6d0185be8a664c57c6d24e7762ed02c28ca"
    env = {k:os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ}
    proc = subprocess.Popen([str(python), "-I", "-S", "-B", "-c", _STAGED_PROCESS,
                             str(candidate), sysconfig.get_paths()["purelib"]], cwd=str(repository), env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    try:
        stdout, stderr = proc.communicate(json.dumps(wire).encode(), timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill();proc.communicate()
        pytest.fail("owned synthetic staged child timed out")
    assert proc.returncode == 0, stderr.decode("utf-8", "replace")
    value = json.loads(stdout)
    assert value["pid"] == proc.pid != os.getpid()
    assert value["code"] == 0 and value["result"]["status"] == "ANSWER"
    assert value["events"].count("in_process_private_reader") == 1
    assert value["events"][0] == value["events"][-1] == "c2:VALIDATED_STAGED"
    record = dict(pid=proc.pid,parent_pid=os.getpid(),returncode=proc.returncode,code=value["code"],
                  events=value["events"],stdout_sha256=sha(stdout),source_pins=wire["source_pins"],
                  scope="Synthetic separate Python process, inner launcher in-process; no E5/live lane proof")
    (Path(os.environ["WD_Q4_RC2_AUDIT"])/"STAGED_PROCESS_RECEIPT.json").write_text(json.dumps(record,indent=2)+"\n")
