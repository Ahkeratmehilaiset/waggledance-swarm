# Pytest Temp / Windows ACL Cleanup Policy

This policy exists because Windows pytest basetemp directories can inherit ACLs
that later block `Remove-Item`, worktree cleanup, or `git worktree prune`.

## Basetemp Rule

Prefer ignored audit scratch for all agent-run pytest commands:

```text
python -m pytest <tests> --basetemp=.codex-audit/pytest_tmp/<task-id>
```

Avoid creating repo-root `.pytest_tmp*` directories in new commands. They are
ignored for legacy cleanup, but `.codex-audit/pytest_tmp/` is the canonical
location because `.codex-audit/` is already scratch-only.

## Cleanup Command

Normal cleanup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\clean_pytest_temp.ps1
```

Windows ACL repair plus cleanup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\clean_pytest_temp.ps1 -RepairAcl
```

Dry run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\clean_pytest_temp.ps1 -WhatIf
```

## Safety Rules

- The cleanup script must run inside a real git worktree.
- It only deletes repo-root `.pytest_tmp*` directories and child directories
  under `.codex-audit/pytest_tmp/`.
- It must use `Remove-Item -LiteralPath`, never shell-composed `cmd /c` deletes.
- `takeown` and `icacls` are used only when `-RepairAcl` is passed and only
  after the target path has passed the pytest-temp allowlist.
- Orphan worktree deletion is a separate operator task. Do not broaden this
  script to delete arbitrary worktree directories.
