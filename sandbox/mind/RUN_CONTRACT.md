# RUN CONTRACT v1 (sandbox, advisory, never an executor)

`run_contract.py` is a pure checker. It refuses a multi-step run plan that has a known hole before any process starts.

- **What a pass means:** a closed plan is `CONTRACT_CLOSED_NOT_EXECUTABLE`. It is never a run permission, a
  measurement or a PASS of anything.
- **No side effects:** the module reads no file, starts no process, reads no clock and opens no socket.
- **No imports from the chain:** it imports nothing from REMEMBER, step 2, the observer or the grader.

**Design basis:**
- Lead's round 1/10 proposal (`R01_PROPOSAL.v1.md` sha256 9D354A02...), from Grok consultation 637a5f9c (answer
  sha256 5b4d5d6e...).
- Implementation choice by fable-5.

## Interface

```python
plan = compile_contract(descriptor, bindings=bindings, adapter_catalog=catalog, obligations=obligations)
```

**The three tables.** All three are already strictly parsed and versioned JSON:
- **Descriptor** (`wd.mind-run-descriptor.v1`): the steps, artifacts and pinned identities of one run.
- **Adapter catalog** (`wd.mind-run-adapters.v1`): the small, separately reviewed list of allowed adapters.
  - An `argv` adapter has a fixed prefix (program and subcommand) and the flags the tool really offers.
  - A `function` adapter is an in-process call with no argv.
- **Obligations** (`wd.mind-run-obligations.v1`): what the Source requires, kept APART from the descriptor.

**Bindings:** every `<TOKEN>` placeholder needs a binding. Nothing falls back to the current lane, a default model or
the running interpreter.

**Result:** `ResolvedPlan`, whose `executable` field is always `False`, or `RunContractRefused(code)`. The message is
the fixed code alone.

| Code | Refused when |
|---|---|
| `graph_invalid` | a key, version or type is wrong, or a name is unknown; two writers write one artifact; two written outputs, or a written output and an external, have one resolved path (two writers by alias); a step produces an external; an input has no producer; the graph has a cycle |
| `binding_unresolved` | an identity differs from the obligations' pin; a placeholder is unbound; a binding value holds a placeholder; an actor is empty |
| `adapter_unavailable` | the adapter is missing; a function is given argv, or argv is given no adapter; the argv leaves the adapter's prefix, uses a flag the adapter does not offer, or is not flag-value pairs |
| `capture_contract_missing` | a process step does not capture stdout, stderr and exit as three of its own outputs; a function step carries a capture record |
| `output_membership_incomplete` | a step's written files (outputs minus captures) differ from the obligation, whether a file is missing or a phantom is added |
| `input_membership_incomplete` | a step does not declare a read the obligation requires |
| `role_binding_incompatible` | an actor resolves to another role, or a step sees an input its role may not see |
| `required_validation_unreached` | an obligated artifact does not reach its obligated step through the graph |
| `semantic_contract_missing` | a step's result contract is not offered by its adapter, or differs from the obligation |

**Order:**
- The checks run in the table's order, except that the graph checks (two writers, cycle) run after the capture
  check. The first failure raises.
- Plan steps are in topological order, with ties broken by step id.
- The descriptor hash is computed over the steps sorted by id, so neither list order nor key order changes the plan.

## Adaptations of the proposal (fable-5)

1. **One more code, `input_membership_incomplete`.** The proposal's table has no code for a step that omits a real
   read, such as compose's blind and primary shortlist files. Folding that case into the output code would hide which
   side is missing.
2. **Invented flags refuse as `adapter_unavailable`.** The catalog lists the flags each adapter really has, so a
   synthesized `--out` or `--bind` refuses before any process starts. An argv after the prefix must be flag-value
   pairs. This fits the Q4 commands; a tool with positional arguments needs its own adapter kind first.
3. **Captures are outputs.** stdout, stderr and exit are artifacts of the step itself, so a later step can consume
   the stdout report, and a grader is judged by its report, never by exit 0.
4. **Identities are pinned twice.** Both the descriptor and the obligations pin the same identities; a mismatch is
   `binding_unresolved`.
   - **Why that is not enough alone:** a flipped tool hash would still pass a check keyed on the hash.
   - **So:** obligations are keyed on step ids. Flipping a hash in both tables never clears a missing edge (test 1).
5. **Exposure is an obligation.** A role is a resolved actor name. The inputs the role may see form an allow-list
   (`exposure`), so a fresh joiner that is shown more than its judge input refuses.
6. **Two writers by alias (Lead round 10 slice A, a fixed defect).**
   - **The defect:** at 56b59f2a, producer uniqueness held per artifact id only. Every artifact path was resolved and
     the result discarded, so `short_a` and `short_b` could name one file and both compile.
   - **Evidence:** Lead's counter-test, written RED first. Three cases failed before the fix: two ids in one step,
     two ids in two steps (also through two placeholders bound to one value), and a written output at an external's
     path.
   - **The rule now:** the resolved paths of all written outputs (outputs minus captures) are distinct, and none is
     the path of an external. Otherwise the plan is `graph_invalid`.
   - **Still allowed:** two external ids may name one read-only file, on purpose.
   - **Outside the rule:** capture streams. v1 has no artifact kind that says a stream's path is a file, so no file
     kind is guessed (see the limits below).

## What this does NOT prove

- **Bytes:** it does not check that the named files exist or hold the bytes the identities say.
- **Origin:** it does not prove where a process came from, and it does not authorize a run.
- **The obligations themselves:** it cannot tell whether the obligations table is right. If one change edits the
  obligations and the test together, a consistent lie still compiles. So:
  - The obligations table is reviewed against the Source on its own.
  - Any real Q4 table is written and reviewed by non-authors of the Q4 candidate.
  - fable-5 wrote that candidate, so this branch ships the mechanism and public toys only, and NO Q4 obligations
    table.
- **Path identity:** the alias rule compares resolved path STRINGS only. It does not detect two spellings of one
  file: case, separators, `..`, an NTFS junction, a symlink, a hard link or a disk-level identity. Proving file
  identity would need the filesystem, which this pure checker never touches.
- **Streams:** two capture streams may share a path string. Nothing in v1 says whether a stream path is a file. A
  rule for streams needs an artifact-kind contract, which would be a new descriptor version.
- **Plan identity:** `descriptor_sha256` and `obligations_sha256` do not identify the whole resolved plan, because
  the adapter catalog, the bindings and the resolved paths are not inside the pair. This is documented, not fixed: a
  stronger binding waits for a consumer that needs it.
- **Everything else stays separate:** full memory, encoder and import identity, the Medium token, technical runner
  isolation, cases and the pre-registered run lock remain separate requirements.

## Tests

- **Command:**
  `C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_run_contract.py -q -p no:cacheprovider`
- **What they use:** public toys only. Every refusal test breaks exactly one rule.
- **Mutant check:** fable-5's audit folders hold the mutant checks, each with a passing unmutated control: 20 mutants
  for v1, then 25 after the alias fix (the 20 again plus five of the new rule).
