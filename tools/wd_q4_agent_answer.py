"""Default-off caller-bound validation around one injected transport call.

This is an in-memory seam, not a launcher, trusted clock, session identity,
publisher authentication, freshness proof, sandbox or production connection.
The caller independently authenticates the binding, source/public-policy,
projection, roster, time and transport, prevents concurrent mutation, and
owns any required cross-process quarantine, watchdog and lease retention.

The transport callable receives ``query_bytes`` and ``query_utc`` as keyword
arguments. Before transport, the strict query document's second-precision UTC
time must denote the same whole second as canonical ``query_utc``. A transport
echo is not proof of execution. It must return exactly ``status``, ``query_utc`` and
``output``. A complete REFUSED receipt has a matching declared time and an
output of exactly None; it means the transport has independently confirmed
cleanup. An OK receipt has a matching time and bytes as output; the bytes must
also validate as an exact answer. Incomplete or contradictory receipts are
UNKNOWN. If cleanup cannot be confirmed the transport must report UNKNOWN.
If it raises, it must retain any lease/quarantine outside the exception object:
this seam does not carry arbitrary objects or exception details. asyncio
cancellation keeps its type; unrecognized BaseException subclasses are sanitized.

The supplied current time and age are only checked against each other BEFORE
the transport; neither wall-clock truth nor freshness at return is proved.
This adapter consumes a JSON query document. The existing session CLI instead
consumes a raw situation and supplies its own datetime.now; this adapter is not
directly wired to that CLI, and a time echo does not establish their agreement.
ABSTAIN never proves absence. Query text must not be logged by the caller or
transport. Raw query, pins, answers and exception details are not emitted by
this seam; caller/transport frames and memory erasure are outside its scope.
"""

import asyncio
import json

from tools.wd_q4_allowed_records import build_allowed_records
from tools.wd_q4_answer_validate import validate_answer
from tools.wd_q4_lane_link import _LANES
from tools.wd_q4_lane_query import _constant, _pairs

_MAX_QUERY_BYTES = 16000


def query_agent_answer(*, lane, query_bytes, projection_bytes, policy_bytes, roster_bytes, expected_binding,
                       query_utc, current_utc, max_age_seconds, transport, enabled=False):
    """Return only a record-bound answer from one caller-owned transport."""
    if enabled is False:
        return {"status": "OFF"}
    failure = "agent_answer_refused"
    entered = False
    records = response = binding = answer = query = None
    try:
        if (enabled is not True or type(lane) is not str or lane not in _LANES
                or type(query_bytes) is not bytes or not 0 < len(query_bytes) <= _MAX_QUERY_BYTES
                or type(expected_binding) is not dict or not callable(transport)):
            raise ValueError()
        query = json.loads(query_bytes.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        if (type(query) is not dict or set(query) != {"situation", "time"}
                or type(query["situation"]) is not str or not query["situation"].strip()
                or len(query["situation"]) > 4000 or type(query["time"]) is not str
                or any(ord(char) < 0x20 or ord(char) == 0x7f for char in query["situation"])
                or type(query_utc) is not str or len(query_utc) != 27
                or not query_utc.endswith(".000000Z")
                or query["time"] != query_utc[:-8] + "Z"):
            raise ValueError()
        binding = dict(expected_binding)
        records = build_allowed_records(projection_bytes=projection_bytes, policy_bytes=policy_bytes,
            roster_bytes=roster_bytes, expected_binding=binding, query_utc=query_utc, current_utc=current_utc,
            max_age_seconds=max_age_seconds, enabled=True)
        entered = True
        # No exception or malformed object after starting the transport may
        # claim a completed cleanup until a complete receipt is checked.
        failure = "agent_answer_unknown"
        response = transport(query_bytes=query_bytes, query_utc=query_utc)
        entered = False
        if (type(response) is not dict or any(type(key) is not str for key in response)
                or set(response) != {"status", "query_utc", "output"}
                or type(response["status"]) is not str or response["status"] not in ("OK", "REFUSED")
                or type(response["query_utc"]) is not str or response["query_utc"] != query_utc
                or (response["status"] == "REFUSED" and response["output"] is not None)
                or (response["status"] == "OK" and type(response["output"]) is not bytes)):
            failure = "agent_answer_unknown"
            raise ValueError()
        # An exact status-specific receipt is the transport's declaration of
        # completion. A rejected answer after a complete OK is a refusal, not
        # a cleanup-unknown claim. Caller still owns actual quarantine/leases.
        failure = "agent_answer_refused"
        if response["status"] == "REFUSED":
            raise ValueError()
        answer = validate_answer(answer_bytes=response["output"], expected_lane=lane,
                                 expected_binding=binding, allowed_records=records, enabled=True)
        return {"status": "OK", "answer": answer, "absence_proven": False,
                "freshness": "UNKNOWN", "caller_authentication": "UNKNOWN"}
    except KeyboardInterrupt:
        failure = "interrupt"
    except SystemExit:
        failure = "exit"
    except GeneratorExit:
        failure = "generator-exit"
    except asyncio.CancelledError:
        failure = "async-cancelled"
    except Exception:
        if entered:
            failure = "agent_answer_unknown"
    except BaseException:
        failure = "base-interrupt"
    lane = query_bytes = projection_bytes = policy_bytes = roster_bytes = expected_binding = None
    query_utc = current_utc = max_age_seconds = transport = enabled = None
    records = response = binding = answer = query = entered = None
    if failure == "interrupt":
        raise KeyboardInterrupt("agent_answer_unknown") from None
    if failure == "exit":
        raise SystemExit("agent_answer_unknown") from None
    if failure == "generator-exit":
        raise GeneratorExit("agent_answer_unknown") from None
    if failure == "async-cancelled":
        raise asyncio.CancelledError("agent_answer_unknown") from None
    if failure == "base-interrupt":
        raise BaseException("agent_answer_unknown") from None
    raise ValueError(failure) from None
