#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${ROOT}" ]]; then
  echo "Run this inside the repository root (Git repo required)."
  exit 1
fi
cd "${ROOT}"

if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI not found."
  echo "Install/login first, then rerun this script."
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${ROOT}/.codex-audit/${STAMP}"
LATEST_LINK="${ROOT}/.codex-audit/latest"
mkdir -p "${OUT_DIR}"
ln -sfn "${OUT_DIR}" "${LATEST_LINK}"

MODEL="${CODEX_MODEL:-gpt-5.4}"
PORT="${AUDIT_PORT:-8011}"

cat > "${OUT_DIR}/schema.json" <<'JSON'
{
  "type": "object",
  "properties": {
    "summary": { "type": "string" },
    "environment": {
      "type": "object",
      "properties": {
        "python_version": { "type": "string" },
        "package_install_method": { "type": "string" },
        "runtime_mode_tested": { "type": "string" },
        "test_commands_run": {
          "type": "array",
          "items": { "type": "string" }
        }
      },
      "required": [
        "python_version",
        "package_install_method",
        "runtime_mode_tested",
        "test_commands_run"
      ],
      "additionalProperties": false
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": { "type": "string" },
          "type": {
            "type": "string",
            "enum": ["confirmed_bug", "suspected_bug", "improvement"]
          },
          "severity": {
            "type": "string",
            "enum": ["critical", "high", "medium", "low"]
          },
          "title": { "type": "string" },
          "evidence": { "type": "string" },
          "reproduction_steps": {
            "type": "array",
            "items": { "type": "string" }
          },
          "likely_root_cause": { "type": "string" },
          "recommended_fix": { "type": "string" },
          "commands_run": {
            "type": "array",
            "items": { "type": "string" }
          },
          "affected_paths": {
            "type": "array",
            "items": { "type": "string" }
          },
          "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          }
        },
        "required": [
          "id",
          "type",
          "severity",
          "title",
          "evidence",
          "reproduction_steps",
          "likely_root_cause",
          "recommended_fix",
          "commands_run",
          "affected_paths",
          "confidence"
        ],
        "additionalProperties": false
      }
    },
    "top_3_next_actions": {
      "type": "array",
      "items": { "type": "string" },
      "minItems": 3,
      "maxItems": 3
    }
  },
  "required": [
    "summary",
    "environment",
    "findings",
    "top_3_next_actions"
  ],
  "additionalProperties": false
}
JSON

cat > "${OUT_DIR}/prompt.md" <<PROMPT
You are performing a runtime-first defect audit of this repository.

Goal:
Find confirmed bugs, suspected bugs, flaky behavior, unsafe behavior, missing guards, performance cliffs, and high-value improvement opportunities based on actual execution of the program.

Context:
- The repository root is the source of truth.
- Read AGENTS.md if present and follow it.
- Primary runtime entrypoint for this repo:
  python -m waggledance.adapters.cli.start_runtime --stub --host 127.0.0.1 --port ${PORT} --log-level info
- Use only local/stub/offline-safe modes. Do not require cloud credentials.
- You may write scratch files, logs, and reports only under .codex-audit/latest/.
- Do not edit source code in this audit run.

Constraints:
- Behavior first, static reading second.
- Reproduce before concluding.
- Prefer the smallest reproduction possible.
- If something is only plausible but not reproduced, mark it as suspected_bug.
- Never claim "fixed" or "verified" without a real command and observed output.
- Keep all evidence inside .codex-audit/latest/.

Mandatory workflow:
1. Inspect the repo just enough to understand how to run it:
   - AGENTS.md
   - README / key docs
   - pyproject.toml
   - requirements-ci.txt / requirements.lock.txt if present
   - .github/workflows/* if relevant to execution
2. Determine the environment setup path in this order:
   a) requirements-ci.txt
   b) requirements.lock.txt
   c) pyproject.toml / editable install
3. Record environment details:
   - python --version
   - pip --version
4. Run behavior-driven checks:
   - python -m compileall waggledance core adapters tests || true
   - import smoke for main entry modules
   - pytest -q -ra --maxfail=25 --durations=25
5. Start the runtime in stub mode on port ${PORT}:
   - Capture stdout/stderr to files under .codex-audit/latest/
   - Wait for startup
   - If the app exposes OpenAPI, fetch /openapi.json
   - Enumerate routes either from OpenAPI or by introspecting the built FastAPI app
   - Exercise representative routes:
     * GET /
     * GET /docs
     * GET /openapi.json
     * any discovered safe GET endpoints
     * malformed/minimal POST payloads for discovered POST endpoints
     * repeated requests to detect flaky or stateful failures
6. If a benchmark or smoke script exists, run the safest baseline variant once.
7. Investigate and log:
   - crashes, tracebacks, non-zero exits
   - silent fallbacks or swallowed exceptions
   - startup warnings that imply broken production path
   - docs-vs-behavior mismatches
   - environment-sensitive or flaky tests
   - port-binding issues, timeouts, hangs, resource leaks
   - suspicious write paths that appear to bypass policy/risk/action-bus style checks
   - missing validation, brittle defaults, poor error messages
8. Save raw artifacts:
   - commands.txt
   - environment.txt
   - test output logs
   - runtime stdout/stderr
   - route inventory
   - request/response samples
   - reproduction snippets
9. Final answer must follow the JSON schema exactly.

Done when:
- The program has actually been run
- The runtime has been exercised through HTTP and/or app route introspection
- Findings are split into confirmed_bug, suspected_bug, and improvement
- Each finding has evidence, reproduction steps, and a concrete improvement/fix suggestion
PROMPT

echo "==> Repo root: ${ROOT}"
echo "==> Audit dir: ${OUT_DIR}"
echo "==> Model: ${MODEL}"

codex exec \
  --cd "${ROOT}" \
  --model "${MODEL}" \
  --ask-for-approval never \
  --sandbox workspace-write \
  --json \
  --output-schema "${OUT_DIR}/schema.json" \
  --output-last-message "${OUT_DIR}/final_report.json" \
  "$(cat "${OUT_DIR}/prompt.md")" \
  > "${OUT_DIR}/codex_events.jsonl" \
  2> "${OUT_DIR}/codex_stderr.log"

echo
echo "Audit complete."
echo "Final report:   ${OUT_DIR}/final_report.json"
echo "Codex events:   ${OUT_DIR}/codex_events.jsonl"
echo "Codex stderr:   ${OUT_DIR}/codex_stderr.log"
echo "Latest link:    ${LATEST_LINK}"
