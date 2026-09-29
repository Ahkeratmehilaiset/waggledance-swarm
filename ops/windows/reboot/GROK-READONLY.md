# Grok repository read-only sessions

The installed `C:\Python\Invoke-WdGrok.ps1` exposes the packaged session controller:

```powershell
& C:\Python\Invoke-WdGrok.ps1 -ReadOnly -TaskId 'review/example' -PromptPath C:\Python\review.md -RepositoryPath C:\Python\project2 -Commit '<full 40-character commit SHA>'
```

`-ReadOnly` enables model-directed `read_file`, `list_dir` and literal `grep`
requests against that immutable Git commit through the bounded Git blob broker.
Git comes from the hash-verified fleet configuration, not PATH. Grok's native
tools remain denied. `-MaxRounds` is 2..8 (default 6); the existing consultation
budget, task exceptions, lifecycle events and lock still apply.

No arguments (or `-Status`) checks the old helper status without a model call.
`-Inventory` alone inventories inherited hooks/MCP/LSP without a model call.
`-PromptPath` without `-ReadOnly` retains the existing text-only advisory mode.
Reboot/startup never automatically initiates a Grok consultation.

The controller is NOT an OS-level read-only sandbox. If inherited executable
components exist, it refuses before consultation unless their exact inventory
digest is deliberately supplied as `-AcknowledgeInheritedSurface`. This is an
acknowledgement of possible inherited execution, not an isolation guarantee;
the wrapper never supplies it automatically. Unreadable inventory fails closed.

Release integration was requested without additional tests or model trials.
The controller's multi-round CLI behavior is not runtime-validated. Installation
and inclusion in the bundle do not establish successful Grok execution.
