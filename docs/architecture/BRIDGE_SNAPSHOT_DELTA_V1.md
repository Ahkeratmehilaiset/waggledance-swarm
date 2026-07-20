# Bridge snapshot/delta reader v1

Status: adapter-core contract. This document does not authorize production
wiring, cursor persistence, sidecar initialization, or log rotation.

## Purpose and boundary

The v1 reader gives Python and Windows PowerShell callers the same bounded,
fail-closed way to read complete JSONL objects. A call is either a snapshot
(no cursor, start at byte zero) or a delta (start at an accepted cursor). The
reader returns a candidate cursor. It never stores or commits that cursor.

The log is opened once per call with read access and sharing that permits the
current writer and atomic replacement. File identity is obtained from that
same handle. On Windows the identity is the volume serial number plus the
64-bit file index returned by GetFileInformationByHandle. Other platforms use
device and inode only so the Python contract can be tested portably.

## Inputs

- path: existing bridge JSONL path. A missing path is IDLE for a new snapshot
  and RETRY for a delta.
- cursor, optional: byte offset, operating-system file identity, and optional
  generation token from a previously accepted candidate.
- max_bytes: positive upper bound on log bytes read during one call, capped at
  64 MiB. The bound includes the LF terminator. A record whose LF is the max_bytes-th byte is
  accepted. max_bytes bytes without an LF is BLOCKED, avoiding a poll loop
  that can never advance.
- generation_path, optional: path to an externally managed generation
  document. The reader only reads this file. It never creates or updates it.

The generation document is strict UTF-8 without BOM and contains exactly one
JSON property:

    {"generation":"opaque-token"}

The document is capped at 512 bytes. The token is 1 through 128 ASCII letters,
digits, dots, underscores, colons, or hyphens. A configured document is read before and after the log snapshot.
The values must agree, and a delta cursor must carry the same value.

## Row and cursor rules

Only bytes through the last complete LF in the bounded read are candidates
for delivery. A preceding CR is accepted as part of CRLF and removed before
JSON parsing. Each row must decode as strict UTF-8 and parse as a JSON object.
The file must not start with a UTF-8 BOM. Blank rows, arrays, scalars,
non-finite floating-point results, integer tokens outside the exact IEEE-754
range -9007199254740991 through 9007199254740991, malformed JSON, invalid
UTF-8, and an oversized unterminated record are BLOCKED. Decimal and exponent
tokens retain the finite floating-point rule. Object and array nesting is
capped at 32 containers, counting the top-level object as one.

Every object property name, at every nesting level, must contain ASCII code
points only, including after JSON escapes are decoded. Property collision
checks then fold ASCII `A` through `Z` to `a` through `z`; both exact
duplicates and collisions after that fold are BLOCKED. Unicode remains valid
in property values. Any JSON `\u` escape whose UTF-16 code unit is in the
surrogate range U+D800 through U+DFFF is BLOCKED, including an escaped
surrogate pair. A supplementary value character encoded directly as valid
UTF-8 remains valid, as does a literal escaped backslash followed by text such
as `uD800`. This avoids adapter-dependent property and surrogate behavior
without excluding valid Unicode bridge values.

Rows and a candidate cursor are returned only after the following checks:

1. generation-before is readable and matches the delta cursor, when enabled;
2. the single log handle has the expected operating-system identity;
3. offset is not past the captured length;
4. a nonzero offset immediately follows an LF;
5. the bounded bytes decode into complete JSON-object rows;
6. file identity queried again from the same handle still equals the identity
   captured before the read;
7. the handle length has not regressed; and
8. generation-after equals generation-before, when enabled.

Every failure leaves the caller's cursor unchanged. BLOCKED and RETRY never
return a candidate cursor or rows. IDLE can return an equal candidate cursor;
committing it is a no-op. The consumer owns row processing and cursor commit,
and must not commit a candidate before processing succeeds.

## Outcomes

| Status | Meaning | Candidate |
| --- | --- | --- |
| OK | One or more complete, valid objects are available. | Next LF boundary. |
| IDLE | No complete new object is available in a stable snapshot. | Equal position when the log exists. |
| RETRY | Continuity could not be established, or the snapshot changed. | None. |
| BLOCKED | Input, cursor, generation document, encoding, framing, or JSON is invalid. | None. |

Reason strings are stable machine-readable details. Adapters should branch on
status and record the reason for diagnosis; they must not silently turn RETRY
or BLOCKED into IDLE.

## Complexity and instrumentation

A delta call validates at most one byte immediately before cursor.offset, then
seeks directly to cursor.offset and reads at most max_bytes. It does not scan
or hash the historical prefix. Result fields bytes_read,
bytes_consumed, read_calls, snapshot_length, and requested_offset make this
observable. bytes_read includes the optional one-byte cursor-boundary probe.
In steady state the log I/O is O(delta), bounded by max_bytes plus one byte;
generation reads are bounded by the small sidecar document.

## Explicit non-guarantees

Operating-system identity detects replacement with a different file. A
generation change detects cooperating rotation before or during a call. The
reader does not claim to detect an arbitrary in-place rewrite in the already
consumed middle of the same file when the writer fails to bump generation.
That guarantee requires the future writer/deployment protocol; sampling or
rehashing history here would both overclaim and destroy O(delta) behavior.

This core also does not initialize a generation, rotate logs, write a cursor,
choose retry timing, wire a production consumer, or upgrade any bridge
authority. Those are separate, operator-gated deployment stages.
