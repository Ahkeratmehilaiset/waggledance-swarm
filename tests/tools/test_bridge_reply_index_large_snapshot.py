"""Reply-index snapshots beyond the canonical reader's 100000-row page bound.

Release-blocker regression: Read-BridgeReplyIndex rebuilt a cold index through Read-BridgeEventSnapshot, whose
row bound is 100000, so a log of 100001 valid rows threw 'Reply snapshot is incomplete; pending/answered cannot
be inferred: snapshot_exceeds_bounds' on every query; the warm path read its whole delta in ONE
Read-BridgeEventDelta call with the same cap. Read-BridgeReplyIndexPages now pages the canonical reader up to the
EOF frozen by its first page and fails the whole snapshot closed on a page refusal, truncation below the frozen
EOF, a page without progress or an unfinished final append.

Every case runs on Windows PowerShell 5.1 and PowerShell 7; a host is skipped only when it is not installed.
Logs, derived caches and PowerShell temp files live under pytest's temporary directories only. Only two cases
per host read real large logs (exactly 100000 rows without cache; 100001 rows with cache, then an append); every
other control, including the named mutant, runs on small logs with a forced small page size in one PowerShell
session per host.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from test_wd_reboot_bundle import ROOT, WINDOWS_POWERSHELL
from test_wd_startup_recovery import q

INDEX = ROOT / '.agent-bridge' / 'bin' / 'BridgeReplyIndex.ps1'
PWSH = shutil.which('pwsh')
HOSTS = [
    pytest.param(WINDOWS_POWERSHELL, id='powershell',
                 marks=pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason='Windows PowerShell 5.1 is not installed')),
    pytest.param(PWSH, id='pwsh', marks=pytest.mark.skipif(PWSH is None, reason='PowerShell 7 (pwsh) is not installed')),
]
PAGE_ROWS = 100000  # Read-BridgeEventDelta's per-call row bound, formerly the bound of the whole cold snapshot
FILLER = b'{"seq":%d,"type":"message"}\n'
LARGE_TIMEOUT = 900
SMALL_TIMEOUT = 300


def _request(request_id):
    from test_bridge_request_contract import events
    _, request, _ = events()
    request.update(request_id=request_id, request_digest=f'digest-{request_id}', task_id=f'fixture/{request_id}')
    return request


def _reply(request_id):
    from test_bridge_request_contract import events
    _, request, reply = events()
    reply.update(in_reply_to_request_id=request_id, in_reply_to_request_digest=f'digest-{request_id}',
                 task_id=f'fixture/{request_id}',
                 in_reply_to_requester={key: request[key] for key in ('agent', 'agent_uuid', 'session_id', 'run_id')})
    return reply


def _filler(seq):
    return {'seq': seq, 'type': 'message'}


def _jsonl(rows):
    # The reader's lexical contract: one JSON object per LF-terminated line, UTF-8 (ASCII here), no BOM.
    return b''.join(json.dumps(row, separators=(',', ':')).encode('ascii') + b'\n' for row in rows)


def _expected(*request_ids):
    expected = {}
    for request_id in request_ids:
        expected[f'{request_id} request'] = (request_id, None)
        expected[f'{request_id} reply'] = (None, request_id)
    return expected


def _keys(summary):
    return [(key['request_id'], key['in_reply_to_request_id']) for key in summary['keys']]


def _completeness_violations(index, expected, parsed_rows, file_length):
    """Every completeness assertion of the large cold index tests; an empty list means complete."""
    keys = _keys(index)
    violations = [f'{label} missing' for label, key in expected.items() if key not in keys]
    if len(keys) != len(expected):
        violations.append(f'{len(keys)} indexed rows, expected {len(expected)}')
    if index['parsed_rows'] != parsed_rows:
        violations.append(f"parsed_rows {index['parsed_rows']} != {parsed_rows} rows in the log")
    if not index['offset'] == index['snapshot_length'] == file_length:
        violations.append(f"cursor offset {index['offset']} / snapshot_length {index['snapshot_length']}"
                          f' / file length {file_length} differ')
    return violations


class _Code(str):
    """PowerShell source inserted verbatim; every other template value is quoted with q()."""


def _render(template, **values):
    for name, value in values.items():
        text = str(value) if isinstance(value, (_Code, int)) else q(value)
        template = template.replace(f'%%{name}%%', text)
    assert '%%' not in template, template
    return template


def _environment(root):
    temp = root / 'ps-temp'
    temp.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith(('AGENT_BRIDGE_', 'WD_'))}
    env.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    return env


def _command(ps, root, name, script):
    path = root / f'{name}.ps1'
    path.write_bytes(script.encode('ascii'))  # bytes: LF stays LF inside the quoted fixture rows
    return [ps, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(path)]


PRELUDE = r"""
$ErrorActionPreference='Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') { $env:PSModulePath=Join-Path $PSHOME 'Modules' }
$FixtureClock=[Diagnostics.Stopwatch]::StartNew()
. %%INDEX%%
$FixtureOriginalDelta=(Get-Command Read-BridgeEventDelta).ScriptBlock
$FixtureOriginalPages=(Get-Command Read-BridgeReplyIndexPages).ScriptBlock
$FixtureUtf8=New-Object Text.UTF8Encoding($false)
$FixtureReport=[ordered]@{}
function Get-FixtureField {
    param($Object,[string]$Name)
    if ($null -eq $Object) { return $null }
    $property=$Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}
function ConvertTo-FixtureSummary {
    param($Result)
    $cursor=Get-FixtureField $Result 'candidate_cursor'
    [ordered]@{
        status=Get-FixtureField $Result 'status'
        reason=Get-FixtureField $Result 'reason'
        pages=Get-FixtureField $Result 'pages'
        parsed_rows=Get-FixtureField $Result 'parsed_rows'
        snapshot_length=Get-FixtureField $Result 'snapshot_length'
        offset=Get-FixtureField $cursor 'offset'
        file_identity=Get-FixtureField $cursor 'file_identity'
        generation=Get-FixtureField $cursor 'generation'
        cache_status=Get-FixtureField $Result 'cache_status'
        prefix_sha256=Get-FixtureField $Result 'prefix_sha256'
        keys=@(foreach ($row in @(Get-FixtureField $Result 'rows')) {
            if ($null -ne $row) {
                [ordered]@{request_id=Get-FixtureField $row 'request_id';in_reply_to_request_id=Get-FixtureField $row 'in_reply_to_request_id'}
            }
        })
    }
}
"""

REPORT = r"""
$FixtureReport.seconds=$FixtureClock.Elapsed.TotalSeconds
$FixtureReport.peak_mb=[Math]::Round([Diagnostics.Process]::GetCurrentProcess().PeakWorkingSet64/1MB)
$FixtureReport | ConvertTo-Json -Depth 64 -Compress
"""


# ---------------------------------------------------------------------------------------------------------------
# Real large logs: exactly one reader page of rows, and one row more. Nothing else reads a large log.
# ---------------------------------------------------------------------------------------------------------------

LARGE_SUMMARY = r"""
function ConvertTo-FixtureLargeIndex {
    param($Result,[string]$Path)
    $summary=ConvertTo-FixtureSummary $Result
    $summary.rows=@(foreach ($row in @(Get-FixtureField $Result 'rows')) { if ($null -ne $row) { $row } })
    $summary.file_length=(Get-Item -LiteralPath $Path).Length
    $summary
}
"""

LARGE_COLD = r"""
$index=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
$FixtureReport.index=ConvertTo-FixtureLargeIndex $index %%LOG%%
"""

LARGE_CACHED = r"""
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
$FixtureReport.first=ConvertTo-FixtureLargeIndex $first %%LOG%%
[IO.File]::AppendAllText(%%LOG%%,%%APPEND%%,$FixtureUtf8)
$second=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
$FixtureReport.second=ConvertTo-FixtureLargeIndex $second %%LOG%%
"""


def _large_log(directory, tag, total_rows):
    """`total_rows` rows: a request/reply pair first, filler, and a late request/reply pair as the LAST two rows.

    With 100001 rows the late request is row 100000 (the last row of the first reader page) and its reply is
    row 100001 (the only row of the second page), so a lost page boundary shows up as a false 'pending'.
    """
    early = [_request(f'{tag}-early'), _reply(f'{tag}-early')]
    late = [_request(f'{tag}-late'), _reply(f'{tag}-late')]
    data = _jsonl(early) + b''.join(FILLER % seq for seq in range(total_rows - 4)) + _jsonl(late)
    assert data.count(b'\n') == total_rows
    directory.mkdir()
    (directory / 'events.jsonl').write_bytes(data)
    appended = [_request(f'{tag}-appended'), _reply(f'{tag}-appended')]
    return dict(log=directory / 'events.jsonl', total_rows=total_rows, file_length=len(data),
                rows=early + late, appended_rows=appended, append=_jsonl(appended),
                indexed=_expected(f'{tag}-early', f'{tag}-late'), appended_keys=_expected(f'{tag}-appended'))


@pytest.fixture(scope='module', params=HOSTS)
def ps(request):
    return request.param


@pytest.fixture(scope='module')
def large(ps, tmp_path_factory):
    root = tmp_path_factory.mktemp('reply-index-large')
    generated = time.monotonic()
    layouts = {'exact': _large_log(root / 'exact', 'exact', PAGE_ROWS),
               'over': _large_log(root / 'over', 'over', PAGE_ROWS + 1)}
    generate_seconds = time.monotonic() - generated
    over = layouts['over']
    scripts = {'exact_nocache': _render(LARGE_COLD, LOG=layouts['exact']['log']),
               'over_cache': _render(LARGE_CACHED, LOG=over['log'], CACHE=root / 'over' / 'cache' / 'reply-index.json',
                                     APPEND=over['append'].decode('ascii'))}
    env = _environment(root)
    processes = {}
    started = time.monotonic()
    try:
        for name, body in scripts.items():  # the two large cases run concurrently
            script = _render(PRELUDE, INDEX=INDEX) + LARGE_SUMMARY + body + REPORT
            processes[name] = subprocess.Popen(_command(ps, root, name, script), cwd=root, env=env, text=True,
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        results = {}
        for name, process in processes.items():
            stdout, stderr = process.communicate(timeout=max(1.0, started + LARGE_TIMEOUT - time.monotonic()))
            results[name] = dict(returncode=process.returncode, stdout=stdout, stderr=stderr)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.kill()
                process.communicate()
    return dict(host=Path(ps).stem, layouts=layouts, results=results, generate_seconds=generate_seconds,
                wall_seconds=time.monotonic() - started)


def _large_report(large, name):
    result = large['results'][name]
    assert result['returncode'] == 0, f"{name} failed on {large['host']}: {result['stderr'][-4000:]}"
    return json.loads(result['stdout'])


def test_cold_index_of_exactly_one_reader_page_of_rows_without_cache(large):
    exact = large['layouts']['exact']
    index = _large_report(large, 'exact_nocache')['index']
    assert _completeness_violations(index, exact['indexed'], exact['total_rows'], exact['file_length']) == []
    assert index['rows'] == exact['rows'] and index['cache_status'] == 'disabled'


def test_cold_index_one_row_beyond_the_reader_page_with_cache_then_incremental(large):
    over = large['layouts']['over']
    report = _large_report(large, 'over_cache')
    first, second = report['first'], report['second']
    # Row 100000 (the last row of the first reader page) is the late request, row 100001 its reply.
    assert first['cache_status'] == 'rebuilt'
    assert _completeness_violations(first, over['indexed'], over['total_rows'], over['file_length']) == []
    assert first['rows'] == over['rows']
    # The incremental call parses only the appended delta yet still returns every indexed row.
    assert second['cache_status'] == 'incremental'
    assert _completeness_violations(second, dict(over['indexed'], **over['appended_keys']), 2,
                                    over['file_length'] + len(over['append'])) == []
    assert second['rows'] == over['rows'] + over['appended_rows']


def test_large_cases_finish_within_the_bound_and_report_wall_time(large):
    reports = {name: _large_report(large, name) for name in large['results']}
    timing = ', '.join(f"{name} {report['seconds']:.1f}s/{report['peak_mb']}MB" for name, report in reports.items())
    print(f"\n[{large['host']}] large logs written in {large['generate_seconds']:.2f}s; "
          f"{len(reports)} concurrent cases finished in {large['wall_seconds']:.1f}s wall ({timing})")
    assert large['wall_seconds'] < LARGE_TIMEOUT


# ---------------------------------------------------------------------------------------------------------------
# Small-page controls: one PowerShell session per host, each case in its own scope and directory.
# ---------------------------------------------------------------------------------------------------------------

SMALL_HELPERS = r"""
function Invoke-FixtureCase {
    param([string]$Name,[scriptblock]$Body)
    try { $FixtureReport[$Name]=[ordered]@{ok=$true;value=(& $Body)} }
    catch { $FixtureReport[$Name]=[ordered]@{ok=$false;error=$_.Exception.Message;at=$_.InvocationInfo.PositionMessage} }
}
function Invoke-FixtureIndex {
    param([scriptblock]$Call)
    try { return [ordered]@{threw=$false;summary=(ConvertTo-FixtureSummary (& $Call))} }
    catch { return [ordered]@{threw=$true;error=$_.Exception.Message} }
}
function Add-FixtureText { param([string]$Path,[string]$Text) [IO.File]::AppendAllText($Path,$Text,$FixtureUtf8) }
function Set-FixtureBytes {
    param([string]$Path,[int64]$Offset,[string]$Text)
    $bytes=[Text.Encoding]::ASCII.GetBytes($Text)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
    try { [void]$stream.Seek($Offset,[IO.SeekOrigin]::Begin); $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
}
function Set-FixtureLength {
    param([string]$Path,[int64]$Length)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
    try { $stream.SetLength($Length) } finally { $stream.Dispose() }
}
function Move-FixtureLog {
    param([string]$Path,[string]$Text)
    [IO.File]::Move($Path,$Path+'.rotated')
    [IO.File]::WriteAllText($Path,$Text,$FixtureUtf8)
}
function Set-FixtureGeneration {
    param([string]$Path,[string]$Generation)
    [IO.File]::WriteAllText($Path,'{"generation":"'+$Generation+'"}',$FixtureUtf8)
}
function Remove-FixtureFile { param([string]$Path) [IO.File]::Delete($Path) }
function Get-FixtureCacheFiles {
    param([string]$CachePath)
    $directory=Split-Path -Parent $CachePath
    @(if (Test-Path -LiteralPath $directory) { Get-ChildItem -LiteralPath $directory -File | ForEach-Object { $_.Name } })
}
function Get-FixtureCacheHashes {
    param([string]$CachePath)
    $directory=Split-Path -Parent $CachePath
    @(if (Test-Path -LiteralPath $directory) {
        Get-ChildItem -LiteralPath $directory -File -Filter '*.json' | Sort-Object Name |
            ForEach-Object { $_.Name + ' ' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
}
"""

# Wraps the canonical page reader inside one case scope: $FixtureHook.after runs right after page number
# after_call returns; $FixtureHook.replace answers every page from replace_from on instead of the reader.
DELTA_WRAPPER = _Code(r"""
function Read-BridgeEventDelta {
    param([AllowNull()][AllowEmptyString()][string]$Path,[AllowNull()]$Cursor=$null,[int64]$MaxBytes=4194304,[int]$MaxRows=10000)
    $FixtureHook.calls++
    if ($FixtureHook.ContainsKey('replace') -and $FixtureHook.calls -ge $FixtureHook.replace_from) {
        return (& $FixtureHook.replace $Path $Cursor)
    }
    $page=& $FixtureOriginalDelta -Path $Path -Cursor $Cursor -MaxBytes $MaxBytes -MaxRows $MaxRows
    if ($FixtureHook.ContainsKey('after') -and $FixtureHook.calls -eq $FixtureHook.after_call) { [void](& $FixtureHook.after) }
    return $page
}
""")


def _small_pages(rows, page_bytes=67108864):
    # Read-BridgeReplyIndex has no page-size parameter; this case-scoped wrapper forces small real pages.
    return _Code(r"""
function Read-BridgeReplyIndexPages {
    param([string]$Path,$Cursor=$null,[int64]$MaxBytes=268435456,[int]$PageBytes=67108864,[int]$PageRows=100000)
    & $FixtureOriginalPages -Path $Path -Cursor $Cursor -MaxBytes $MaxBytes -PageBytes %d -PageRows %d
}
""" % (page_bytes, rows))


def _small_rows():
    return [_request('alpha'), _filler(1), _reply('alpha'), _filler(2), _request('beta'), _filler(3), _reply('beta')]


BASE = [('alpha', None), (None, 'alpha'), ('beta', None), (None, 'beta')]
GAMMA = [('gamma', None), (None, 'gamma')]
DELTA = [('delta', None), (None, 'delta')]

SMALL_CASES = {
    'frozen_eof_pages': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Add-FixtureText %%LOG%% %%APPEND%% }}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
$pageCalls=$FixtureHook.calls
$after=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);page_calls=$pageCalls;after=(ConvertTo-FixtureSummary $after);
    length=(Get-Item -LiteralPath %%LOG%%).Length}
""",
    # RCO2 C1: one complete row appended after page 1 returns and before page 2; LOG2 is the twin without it.
    'frozen_eof_index': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Add-FixtureText %%LOG%% %%APPEND_ONE%% }}
%%DELTA_WRAPPER%%
%%PAGES3%%
$first=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
$firstCalls=$FixtureHook.calls
$twin=Read-BridgeReplyIndex -Path %%LOG2%% -NoCache
$second=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{first=(ConvertTo-FixtureSummary $first);first_calls=$firstCalls;twin=(ConvertTo-FixtureSummary $twin);
    second=(ConvertTo-FixtureSummary $second)}
""",
    # RCO2 C2: cold path with -CachePath; a change after page 1 must throw and leave no cache file.
    'cold_rewrite_one_byte_in_page_1': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureBytes %%LOG%% %%ALPHA_AT%% 'A' }}
%%DELTA_WRAPPER%%
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
[ordered]@{index=$index;calls=$FixtureHook.calls;cache_files=(Get-FixtureCacheFiles %%CACHE%%)}
""",
    'cold_rename_identical_bytes': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Move-FixtureLog %%LOG%% %%SAME%% }}
%%DELTA_WRAPPER%%
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
[ordered]@{index=$index;calls=$FixtureHook.calls;cache_files=(Get-FixtureCacheFiles %%CACHE%%)}
""",
    'cold_generation_changed': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureGeneration %%GEN%% 'gen-b' }}
%%DELTA_WRAPPER%%
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
[ordered]@{index=$index;calls=$FixtureHook.calls;cache_files=(Get-FixtureCacheFiles %%CACHE%%)}
""",
    # RCO2 C3: an oversized row, an injected cursor beyond the frozen EOF, and rows filling exactly 2 pages.
    'oversized_row': r"""
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageBytes 200
%%PAGES_200_BYTES%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=$index;cache_files=(Get-FixtureCacheFiles %%CACHE%%)}
""",
    'cursor_beyond_frozen_eof': r"""
$FixtureHook=@{calls=0;replace_from=2;replace={
    param($Path,$Cursor)
    $length=[int64](Get-Item -LiteralPath $Path).Length
    New-BridgeLogReadResult -Status 'OK' -Reason 'rows_available' -RequestedOffset ([int64]$Cursor.offset) -Rows @() `
        -CandidateCursor ([pscustomobject]@{offset=$length+7;file_identity=$Cursor.file_identity;generation=$Cursor.generation}) `
        -SnapshotLength $length
}}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
$FixtureHook.calls=0
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=$index;cache_files=(Get-FixtureCacheFiles %%CACHE%%)}
""",
    'rows_fill_exactly_two_pages': r"""
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
%%PAGES3%%
$index=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=(ConvertTo-FixtureSummary $index);length=(Get-Item -LiteralPath %%LOG%%).Length}
""",
    'frozen_eof_warm': r"""
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Add-FixtureText %%LOG%% %%DELTA%%
$FixtureHook=@{calls=0;after_call=1;after={ Add-FixtureText %%LOG%% %%APPEND%% }}
%%DELTA_WRAPPER%%
%%PAGES3%%
$second=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
$secondCalls=$FixtureHook.calls
$third=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
[ordered]@{first=(ConvertTo-FixtureSummary $first);second=(ConvertTo-FixtureSummary $second);second_calls=$secondCalls;
    third=(ConvertTo-FixtureSummary $third)}
""",
    # Named mutant first_page_claims_complete on a page-bound+1 log (page bound 3, as 100000 is for real logs):
    # ONE page, keep its request/reply rows, then CLAIM completeness at the file length. Read-BridgeReplyIndex's
    # own offset == snapshot_length gate accepts the claim; only the completeness assertions can reject it.
    'first_page_claims_complete_mutant': r"""
%%PAGES3%%
$real=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
function Read-BridgeReplyIndexPages {
    param([string]$Path,$Cursor=$null,[int64]$MaxBytes=268435456,[int]$PageBytes=67108864,[int]$PageRows=100000)
    $page=Read-BridgeEventDelta -Path $Path -Cursor $Cursor -MaxBytes $PageBytes -MaxRows 3
    $kept=@(foreach ($row in @($page.rows)) {
        if ((Get-BridgeContractField $row 'request_id') -or (Get-BridgeContractField $row 'in_reply_to_request_id')) { $row }
    })
    $length=[int64](Get-Item -LiteralPath $Path).Length
    [pscustomobject]@{status=$page.status;reason=$page.reason;rows=$kept;parsed_rows=@($page.rows).Count;pages=1;
        candidate_cursor=[pscustomobject]@{offset=$length;file_identity=$page.candidate_cursor.file_identity;
            generation=$page.candidate_cursor.generation};snapshot_length=$length}
}
$mutant=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{real=(ConvertTo-FixtureSummary $real);mutant=(ConvertTo-FixtureSummary $mutant);length=(Get-Item -LiteralPath %%LOG%%).Length}
""",
    'rewrite_between_index_calls': r"""
%%PAGES2%%
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Set-FixtureBytes %%LOG%% %%ALPHA_AT%% 'omega'
$second=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{first=(ConvertTo-FixtureSummary $first);second=$second;reference=(ConvertTo-FixtureSummary $reference)}
""",
    'rewrite_between_cold_pages': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureBytes %%LOG%% %%ALPHA_AT%% 'omega' }}
%%DELTA_WRAPPER%%
%%PAGES2%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{index=$index;calls=$FixtureHook.calls;reference=(ConvertTo-FixtureSummary $reference)}
""",
    'rewrite_cached_prefix_between_warm_pages': r"""
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Add-FixtureText %%LOG%% %%DELTA%%
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureBytes %%LOG%% %%ALPHA_AT%% 'omega' }}
%%DELTA_WRAPPER%%
%%PAGES2%%
$second=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$secondCalls=$FixtureHook.calls
$third=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{second=$second;second_calls=$secondCalls;third=$third;reference=(ConvertTo-FixtureSummary $reference)}
""",
    # RCO2 early read of PR1908: one byte rewritten inside the warm delta after its page was parsed.
    'rewrite_delta_between_warm_pages': r"""
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Add-FixtureText %%LOG%% %%DELTA%%
$cacheBefore=Get-FixtureCacheHashes %%CACHE%%
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureBytes %%LOG%% %%DELTA_AT%% 'sigma' }}
%%DELTA_WRAPPER%%
%%PAGES2%%
$second=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$secondCalls=$FixtureHook.calls
$cacheAfter=Get-FixtureCacheHashes %%CACHE%%
$FixtureHook.Remove('after')
$third=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{first=(ConvertTo-FixtureSummary $first);second=$second;second_calls=$secondCalls;cache_before=$cacheBefore;
    cache_after=$cacheAfter;third=$third;reference=(ConvertTo-FixtureSummary $reference)}
""",
    'truncate_within_frozen_window': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureLength %%LOG%% %%CUT%% }}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
$FixtureHook.calls=0
$FixtureHook.after={ Set-FixtureLength %%LOG2%% %%CUT%% }
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG2%% -NoCache }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=$index}
""",
    'truncate_below_first_page_cursor': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureLength %%LOG%% %%CUT%% }}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
$FixtureHook.calls=0
$FixtureHook.after={ Set-FixtureLength %%LOG2%% %%CUT%% }
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG2%% -NoCache }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=$index}
""",
    'rotate_same_prefix_between_index_calls': r"""
%%PAGES2%%
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Move-FixtureLog %%LOG%% %%ROTATED%%
$second=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{first=(ConvertTo-FixtureSummary $first);second=$second;reference=(ConvertTo-FixtureSummary $reference)}
""",
    'rotate_dropping_a_reply_between_index_calls': r"""
%%PAGES2%%
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Move-FixtureLog %%LOG%% %%ROTATED%%
$second=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{first=(ConvertTo-FixtureSummary $first);second=$second;reference=(ConvertTo-FixtureSummary $reference)}
""",
    'rotate_between_warm_pages': r"""
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Add-FixtureText %%LOG%% %%DELTA%%
$FixtureHook=@{calls=0;after_call=1;after={ Move-FixtureLog %%LOG%% %%ROTATED%% }}
%%DELTA_WRAPPER%%
%%PAGES2%%
$second=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{first=(ConvertTo-FixtureSummary $first);second=$second;reference=(ConvertTo-FixtureSummary $reference)}
""",
    'generation_changed_between_pages': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Set-FixtureGeneration %%GEN%% 'gen-b' }}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
$FixtureHook.calls=0
$FixtureHook.after={ Set-FixtureGeneration %%GEN2%% 'gen-b' }
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG2%% -NoCache }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=$index}
""",
    'generation_removed_between_pages': r"""
$FixtureHook=@{calls=0;after_call=1;after={ Remove-FixtureFile %%GEN%% }}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
[ordered]@{pages=(ConvertTo-FixtureSummary $pages)}
""",
    'generation_changed_on_warm_cache': r"""
%%PAGES2%%
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
Set-FixtureGeneration %%GEN%% 'gen-b'
$second=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%% }
$reference=Read-BridgeReplyIndex -Path %%LOG%% -NoCache
[ordered]@{first=(ConvertTo-FixtureSummary $first);second=$second;reference=(ConvertTo-FixtureSummary $reference)}
""",
    'no_progress': r"""
$FixtureHook=@{calls=0;replace_from=2;replace={
    param($Path,$Cursor)
    New-BridgeLogReadResult -Status 'OK' -Reason 'rows_available' -RequestedOffset ([int64]$Cursor.offset) -Rows @() `
        -CandidateCursor $Cursor -SnapshotLength ([int64](Get-Item -LiteralPath $Path).Length)
}}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
$pageCalls=$FixtureHook.calls
$FixtureHook.calls=0
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -NoCache }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);page_calls=$pageCalls;index=$index}
""",
    'null_cursor_after_first_page': r"""
$FixtureHook=@{calls=0;replace_from=2;replace={
    param($Path,$Cursor)
    New-BridgeLogReadResult -Status 'IDLE' -Reason 'no_rows' -RequestedOffset ([int64]$Cursor.offset) `
        -SnapshotLength ([int64](Get-Item -LiteralPath $Path).Length)
}}
%%DELTA_WRAPPER%%
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
[ordered]@{pages=(ConvertTo-FixtureSummary $pages)}
""",
    'exceeds_max_bytes': r"""
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -MaxBytes 64
[ordered]@{pages=(ConvertTo-FixtureSummary $pages)}
""",
    'partial_final_append': r"""
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -NoCache }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=$index}
""",
    'invalid_row_on_a_later_page': r"""
$pages=Read-BridgeReplyIndexPages -Path %%LOG%% -PageRows 3
%%PAGES3%%
$index=Invoke-FixtureIndex { Read-BridgeReplyIndex -Path %%LOG%% -NoCache }
[ordered]@{pages=(ConvertTo-FixtureSummary $pages);index=$index}
""",
}


@pytest.fixture(scope='module')
def small(ps, tmp_path_factory):
    root = tmp_path_factory.mktemp('reply-index-small')
    rows = _small_rows()
    base = _jsonl(rows)
    ends = list(itertools.accumulate(len(_jsonl([row])) for row in rows))
    append = _jsonl([_request('gamma'), _filler(4), _reply('gamma')])
    delta = _jsonl([_request('delta'), _reply('delta')] + [_filler(seq) for seq in range(5, 9)])
    marker = b'"request_id":"'
    meta = dict(base=len(base), append=len(append), delta=len(delta), rotated_same_prefix=len(base + append))
    # Per case: canonical log bytes, number of independent log directories, generation token, extra values.
    plan = {
        'frozen_eof_pages': (base, 1, None, {}),
        'frozen_eof_index': (base, 2, None, {}),
        'cold_rewrite_one_byte_in_page_1': (base, 1, None, {}),
        'cold_rename_identical_bytes': (base, 1, None, {'SAME': base.decode('ascii')}),
        'cold_generation_changed': (base, 1, 'gen-a', {}),
        'oversized_row': (_jsonl([_filler(1), dict(_filler(2), blob='x' * 400), _filler(3)]), 1, None, {}),
        'cursor_beyond_frozen_eof': (base, 1, None, {}),
        'rows_fill_exactly_two_pages': (_jsonl(rows[:3] + rows[4:]), 1, None, {}),
        'frozen_eof_warm': (base, 1, None, {}),
        'first_page_claims_complete_mutant': (
            _jsonl([_request('early'), _reply('early'), _request('late'), _reply('late')]), 1, None, {}),
        'rewrite_between_index_calls': (base, 1, None, {}),
        'rewrite_between_cold_pages': (base, 1, None, {}),
        'rewrite_cached_prefix_between_warm_pages': (base, 1, None, {}),
        'rewrite_delta_between_warm_pages': (
            base, 1, None, {'DELTA_AT': len(base) + delta.index(marker + b'delta"') + len(marker)}),
        'truncate_within_frozen_window': (base, 2, None, {'CUT': ends[4]}),
        'truncate_below_first_page_cursor': (base, 2, None, {'CUT': ends[1]}),
        'rotate_same_prefix_between_index_calls': (base, 1, None, {'ROTATED': (base + append).decode('ascii')}),
        'rotate_dropping_a_reply_between_index_calls': (
            base, 1, None, {'ROTATED': _jsonl(rows[:2] + [_filler(9)] + rows[3:]).decode('ascii')}),
        'rotate_between_warm_pages': (
            base, 1, None, {'ROTATED': (base + _jsonl([_filler(seq) for seq in range(10, 16)])).decode('ascii')}),
        'generation_changed_between_pages': (base, 2, 'gen-a', {}),
        'generation_removed_between_pages': (base, 1, 'gen-a', {}),
        'generation_changed_on_warm_cache': (base, 1, 'gen-a', {}),
        'no_progress': (base, 1, None, {}),
        'null_cursor_after_first_page': (base, 1, None, {}),
        'exceeds_max_bytes': (base, 1, None, {}),
        'partial_final_append': (base + b'{"seq":99,"type":"mess', 1, None, {}),
        'invalid_row_on_a_later_page': (_jsonl(rows[:3]) + b'{invalid}\n' + _jsonl(rows[4:]), 1, None, {}),
    }
    assert set(plan) == set(SMALL_CASES)
    blocks = []
    for name, (data, copies, generation, extra) in plan.items():
        meta.setdefault('paths', {})[name] = str(root / name / 'log1' / 'events.jsonl')
        values = dict(DELTA_WRAPPER=DELTA_WRAPPER, PAGES2=_small_pages(2), PAGES3=_small_pages(3),
                      PAGES_200_BYTES=_small_pages(100000, 200), APPEND_ONE=_jsonl([_request('gamma')]).decode('ascii'),
                      APPEND=append.decode('ascii'), DELTA=delta.decode('ascii'),
                      ALPHA_AT=base.index(marker + b'alpha"') + len(marker), **extra)
        for number in range(1, copies + 1):
            directory = root / name / f'log{number}'
            directory.mkdir(parents=True)
            (directory / 'events.jsonl').write_bytes(data)
            if generation:
                (directory / 'events.generation.json').write_bytes(b'{"generation":"%s"}' % generation.encode())
            suffix = '' if number == 1 else str(number)
            values.update({f'LOG{suffix}': directory / 'events.jsonl', f'GEN{suffix}': directory / 'events.generation.json',
                           f'CACHE{suffix}': directory / 'cache' / 'reply-index.json'})
        body = _render(SMALL_CASES[name], **{key: value for key, value in values.items()
                                             if f'%%{key}%%' in SMALL_CASES[name]})
        blocks.append(f'Invoke-FixtureCase {q(name)} {{\n{body}\n}}\n')
    script = _render(PRELUDE, INDEX=INDEX) + SMALL_HELPERS + ''.join(blocks) + REPORT
    started = time.monotonic()
    completed = subprocess.run(_command(ps, root, 'small-session', script), cwd=root, env=_environment(root),
                               capture_output=True, text=True, timeout=SMALL_TIMEOUT)
    assert completed.returncode == 0, completed.stderr[-4000:]
    return dict(host=Path(ps).stem, report=json.loads(completed.stdout), meta=meta,
                wall_seconds=time.monotonic() - started)


def _value(small, name):
    outcome = small['report'][name]
    assert outcome['ok'], f"{name} failed on {small['host']}: {outcome['error']}\n{outcome.get('at')}"
    return outcome['value']


def _completed(call):
    assert not call['threw'], call['error']
    return call['summary']


def test_frozen_eof_excludes_rows_appended_after_the_first_page(small):
    meta = small['meta']
    before, after = meta['base'], meta['base'] + meta['append']
    value = _value(small, 'frozen_eof_pages')
    pages = value['pages']
    assert (pages['status'], pages['pages'], value['page_calls'], pages['parsed_rows']) == ('OK', 3, 3, 7)
    assert pages['offset'] == pages['snapshot_length'] == before and value['length'] == after
    assert _keys(pages) == BASE  # the appended request and reply are not in this snapshot
    following = value['after']
    assert following['offset'] == following['snapshot_length'] == after and following['parsed_rows'] == 10
    assert _keys(following) == BASE + GAMMA


def test_frozen_eof_holds_through_read_bridge_reply_index_cold_and_warm(small):
    meta = small['meta']
    # RCO2 C1: one complete row appended after page 1 returned and before page 2 was read.
    cold = _value(small, 'frozen_eof_index')
    first, twin, second = cold['first'], cold['twin'], cold['second']
    assert cold['first_calls'] == 3
    assert first['offset'] == first['snapshot_length'] == meta['base']
    assert (first['parsed_rows'], _keys(first)) == (7, BASE)  # the appended row is absent
    log = Path(meta['paths']['frozen_eof_index'])
    assert first['prefix_sha256'] == hashlib.sha256(log.read_bytes()[:meta['base']]).hexdigest().upper()
    same = ('status', 'pages', 'parsed_rows', 'snapshot_length', 'offset', 'generation', 'cache_status',
            'prefix_sha256', 'keys')
    assert {key: first[key] for key in same} == {key: twin[key] for key in same}  # twin log without the append
    assert second['offset'] == second['snapshot_length'] == log.stat().st_size > meta['base']
    assert (second['parsed_rows'], _keys(second)) == (8, BASE + [('gamma', None)])
    warm = _value(small, 'frozen_eof_warm')
    frozen = meta['base'] + meta['delta']
    assert warm['first']['cache_status'] == 'rebuilt'
    second = warm['second']
    assert (second['cache_status'], warm['second_calls'], second['parsed_rows']) == ('incremental', 2, 6)
    assert second['offset'] == second['snapshot_length'] == frozen and _keys(second) == BASE + DELTA
    third = warm['third']
    assert (third['cache_status'], third['parsed_rows']) == ('incremental', 3)
    assert third['snapshot_length'] == frozen + meta['append'] and _keys(third) == BASE + DELTA + GAMMA


def test_first_page_claims_complete_mutant_is_rejected_by_the_completeness_assertions(small):
    value = _value(small, 'first_page_claims_complete_mutant')
    expected, length = _expected('early', 'late'), value['length']
    assert _completeness_violations(value['real'], expected, 4, length) == []
    mutant = value['mutant']
    assert mutant['offset'] == mutant['snapshot_length'] == length  # claimed complete, and the index accepted it
    violations = _completeness_violations(mutant, expected, 4, length)
    assert 'late reply missing' in violations, violations
    assert 'parsed_rows 3 != 4 rows in the log' in violations, violations


def _cache_json(value):
    return [name for name in value['cache_files'] if name.endswith('.json')]  # the .lock may remain


@pytest.mark.parametrize('case', ['oversized_row', 'cursor_beyond_frozen_eof'])
def test_oversized_row_or_cursor_beyond_the_frozen_eof_is_an_incomplete_refusal_without_cache(small, case):
    value = _value(small, case)
    pages = value['pages']
    if case == 'oversized_row':
        assert (pages['status'], pages['reason']) == ('BLOCKED', 'record_exceeds_max_bytes')
    # Neither paged result passes the index's completeness gate, and the index refuses without a cache.
    assert pages['status'] in ('BLOCKED', 'RETRY') or pages['offset'] != pages['snapshot_length']
    assert value['index']['threw'] and 'pending/answered cannot be inferred' in value['index']['error']
    assert _cache_json(value) == [], value['cache_files']


def test_rows_filling_exactly_two_pages_succeed(small):
    value = _value(small, 'rows_fill_exactly_two_pages')
    pages, index, length = value['pages'], value['index'], value['length']
    assert (pages['status'], pages['pages'], pages['parsed_rows']) == ('OK', 2, 6)
    assert pages['offset'] == pages['snapshot_length'] == length
    assert (index['cache_status'], index['parsed_rows'], _keys(index)) == ('rebuilt', 6, BASE)
    assert index['offset'] == index['snapshot_length'] == length


@pytest.mark.parametrize('case', ['cold_rewrite_one_byte_in_page_1', 'cold_rename_identical_bytes',
                                  'cold_generation_changed'])
def test_cold_change_after_page_1_throws_and_leaves_no_cache_file(small, case):
    value = _value(small, case)
    assert value['calls'] >= 2 and value['index']['threw'], value
    assert _cache_json(value) == [], value['cache_files']
    error = value['index']['error']
    print(f"\n[{small['host']}] {case}: {error}")
    if case == 'cold_rewrite_one_byte_in_page_1':
        assert 'Canonical prefix changed' in error
    elif case == 'cold_generation_changed':
        assert error.endswith('generation_changed')


@pytest.mark.parametrize('case', ['rewrite_between_index_calls', 'rewrite_between_cold_pages',
                                  'rewrite_cached_prefix_between_warm_pages'])
def test_same_length_prefix_rewrite_never_returns_stale_rows_as_complete(small, case):
    value = _value(small, case)
    reference = value['reference']
    assert ('omega', None) in _keys(reference) and ('alpha', None) not in _keys(reference)
    if case == 'rewrite_between_index_calls':
        assert ('alpha', None) in _keys(value['first'])
        second = value['second']
        # Throwing is allowed; returning is allowed only as a rebuild equal to a fresh uncached read.
        if not second['threw']:
            assert second['summary']['cache_status'] == 'rebuilt'
            assert _keys(second['summary']) == _keys(reference)
    elif case == 'rewrite_between_cold_pages':
        assert value['calls'] >= 2 and value['index']['threw']
        assert 'prefix changed' in value['index']['error']
    else:
        assert value['second_calls'] >= 2 and value['second']['threw']
        assert 'prefix changed' in value['second']['error']
        third = _completed(value['third'])
        assert third['cache_status'] == 'rebuilt' and _keys(third) == _keys(reference)


def test_rewrite_inside_the_warm_delta_after_its_page_is_refused_and_keeps_the_cache(small):
    # RCO2 early read of PR1908: the warm path hashes what exists before paging, as the cold path does, so a byte
    # rewritten in the delta after its page was parsed is refused and never cached as complete.
    value = _value(small, 'rewrite_delta_between_warm_pages')
    assert value['first']['cache_status'] == 'rebuilt' and _keys(value['first']) == BASE
    second = value['second']
    assert value['second_calls'] >= 2 and second['threw'], second
    assert 'Canonical prefix changed while reading the reply delta' in second['error']
    assert value['cache_before'] and value['cache_after'] == value['cache_before']  # no cache file changed
    reference = value['reference']
    assert ('sigma', None) in _keys(reference) and ('delta', None) not in _keys(reference)
    third = _completed(value['third'])
    assert third['cache_status'] == 'incremental' and _keys(third) == _keys(reference)


@pytest.mark.parametrize('case', ['truncate_within_frozen_window', 'truncate_below_first_page_cursor'])
def test_truncation_below_the_frozen_eof_between_pages_is_retry_log_truncated(small, case):
    value = _value(small, case)
    assert (value['pages']['status'], value['pages']['reason']) == ('RETRY', 'log_truncated')
    assert value['pages']['offset'] is None
    assert value['index']['threw'] and 'truncated' in value['index']['error']


@pytest.mark.parametrize('case', ['rotate_same_prefix_between_index_calls',
                                  'rotate_dropping_a_reply_between_index_calls', 'rotate_between_warm_pages'])
def test_rotation_on_a_warm_cache_gives_no_stale_credit(small, case):
    value = _value(small, case)
    first, reference = value['first'], value['reference']
    second = _completed(value['second'])
    assert first['cache_status'] == 'rebuilt' and _keys(first) == BASE
    assert second['cache_status'] == 'rebuilt'  # never 'incremental' across a file identity change
    assert second['file_identity'] == reference['file_identity'] != first['file_identity']
    assert _keys(second) == _keys(reference)
    assert second['offset'] == second['snapshot_length'] == reference['snapshot_length']
    if case == 'rotate_same_prefix_between_index_calls':
        assert _keys(second) == BASE + GAMMA
        assert second['snapshot_length'] == small['meta']['rotated_same_prefix']
    elif case == 'rotate_dropping_a_reply_between_index_calls':
        assert _keys(second) == [('alpha', None), ('beta', None), (None, 'beta')]  # no credit for the old reply
    else:
        assert _keys(second) == BASE  # nothing from the old file's first delta page


@pytest.mark.parametrize('case', ['generation_changed_between_pages', 'generation_removed_between_pages',
                                  'generation_changed_on_warm_cache'])
def test_changed_generation_is_refused(small, case):
    value = _value(small, case)
    if case == 'generation_changed_between_pages':
        assert (value['pages']['status'], value['pages']['reason']) == ('RETRY', 'generation_changed')
        assert value['index']['threw'] and 'generation_changed' in value['index']['error']
    elif case == 'generation_removed_between_pages':
        assert (value['pages']['status'], value['pages']['reason']) == ('BLOCKED', 'generation_configuration_changed')
    else:
        assert value['first']['generation'] == 'gen-a'
        second = _completed(value['second'])
        assert (second['cache_status'], second['generation']) == ('rebuilt', 'gen-b')
        assert _keys(second) == _keys(value['reference']) == BASE


def test_page_without_progress_is_blocked_and_the_index_throws(small):
    value = _value(small, 'no_progress')
    assert (value['pages']['status'], value['pages']['reason'], value['page_calls']) == (
        'BLOCKED', 'snapshot_no_progress', 2)
    assert value['index']['threw'] and value['index']['error'].endswith('snapshot_no_progress')
    assert 'pending/answered cannot be inferred' in value['index']['error']


@pytest.mark.parametrize('case', ['null_cursor_after_first_page', 'exceeds_max_bytes', 'partial_final_append',
                                  'invalid_row_on_a_later_page'])
def test_paged_reader_refusal_contract(small, case):
    value = _value(small, case)
    pages = value['pages']
    base = small['meta']['base']
    if case == 'null_cursor_after_first_page':
        assert (pages['status'], pages['reason'], pages['offset']) == ('BLOCKED', 'snapshot_incomplete', None)
    elif case == 'exceeds_max_bytes':
        assert (pages['status'], pages['reason'], pages['snapshot_length']) == ('BLOCKED', 'snapshot_exceeds_bounds', base)
    elif case == 'partial_final_append':
        # The cursor stops before the unfinished final row; the index refuses to call that complete.
        assert (pages['status'], pages['reason'], pages['offset']) == ('IDLE', 'partial_record', base)
        assert pages['snapshot_length'] > base
        assert value['index']['threw'] and value['index']['error'].endswith('partial_record')
    else:
        assert (pages['status'], pages['reason']) == ('BLOCKED', 'invalid_json')
        assert value['index']['threw'] and value['index']['error'].endswith('invalid_json')

# Serialized-envelope size, not content characters: both cache directions share
# one bounded Source policy. All data below is synthetic, never a live bridge.
@pytest.fixture(scope="module")
def serialized_cache_roundtrip(ps, tmp_path_factory):
    root = tmp_path_factory.mktemp("reply-cache-envelope-large")
    log = root / "events.jsonl"
    log.write_bytes(_jsonl(_request(f"size-{i}") for i in range(144)))
    script = _render(PRELUDE, INDEX=INDEX) + _render(r"""
# Synthetic size-only pager seam: inflate retained row values AFTER the real
# small canonical page has supplied its cursor, fingerprint and completeness.
# This tests the writer's admitted-payload/read-back policy, not row provenance;
# the existing real-log controls independently test canonical row correctness.
function Read-BridgeReplyIndexPages {
    param([string]$Path,$Cursor=$null,[int64]$MaxBytes=268435456,
        [int]$PageBytes=67108864,[int]$PageRows=100000)
    $snapshot=& $FixtureOriginalPages -Path $Path -Cursor $Cursor -MaxBytes $MaxBytes -PageBytes $PageBytes -PageRows $PageRows
    foreach($row in @($snapshot.rows)){ $row.message='"' * 100000 }
    return $snapshot
}
$first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
$second=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%CACHE%%
$FixtureReport.first=ConvertTo-FixtureSummary $first
$FixtureReport.second=ConvertTo-FixtureSummary $second
$FixtureReport.envelope_bytes=(Get-Item -LiteralPath $first.cache_path).Length
""", LOG=log, CACHE=root / "cache" / "reply-index.json") + REPORT
    done = subprocess.run(_command(ps, root, "serialized-roundtrip", script), cwd=root,
                          env=_environment(root), capture_output=True, text=True, timeout=SMALL_TIMEOUT)
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout)


def test_serialized_cache_larger_than_57mb_is_readable_after_its_own_write(serialized_cache_roundtrip):
    report = serialized_cache_roundtrip
    assert 57060172 < report["envelope_bytes"] <= 67108864
    first, second = report["first"], report["second"]
    assert first["cache_status"] == "rebuilt" and first["parsed_rows"] == 144
    assert second["cache_status"] == "incremental" and second["parsed_rows"] == 0
    assert second["keys"] == first["keys"] and len(second["keys"]) == 144
    assert second["offset"] == second["snapshot_length"] == first["snapshot_length"]


@pytest.fixture(scope="module")
def serialized_cache_boundaries(ps, tmp_path_factory):
    root = tmp_path_factory.mktemp("reply-cache-envelope-boundaries")
    log = root / "events.jsonl"
    # Decoding yields real Unicode plus backslashes/quotes; the envelope has
    # UTF-8 multibyte characters AND JSON escaping overhead.
    log.write_bytes(_jsonl([dict(_request("size-boundary"), message=('\u96ea"\\' * 128))]))
    script = _render(PRELUDE, INDEX=INDEX) + SMALL_HELPERS + _render(r"""
$policy=Get-Command Get-BridgeReplyCacheMaxBytes -ErrorAction SilentlyContinue
$FixtureReport.default_policy=$(if($null -ne $policy){& $policy}else{$null})
# An internal test seam lowers ONLY the byte policy for fast exact boundaries;
# the real reader, writer, identity/hash/schema and snapshot checks still run.
$script:FixtureLimit=[int64]67108864
function Get-BridgeReplyCacheMaxBytes { return $script:FixtureLimit }
$seed=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%SEED%%
$envelope=[IO.File]::ReadAllText($seed.cache_path)
$size=[int64](Get-Item -LiteralPath $seed.cache_path).Length
$FixtureReport.physical_bytes=$size
$FixtureReport.utf8_bytes=[Text.Encoding]::UTF8.GetByteCount($envelope)
$FixtureReport.utf16_chars=$envelope.Length
foreach($margin in @(1,0,-1)){
    $script:FixtureLimit=$size+$margin
    $name=if($margin -eq 1){'limit_minus_one'}elseif($margin -eq 0){'equal'}else{'limit_plus_one'}
    $target=Join-Path %%CACHE_ROOT%% ($name+'.json')
    $first=Read-BridgeReplyIndex -Path %%LOG%% -CachePath $target
    $second=Read-BridgeReplyIndex -Path %%LOG%% -CachePath $target
    $exists=Test-Path -LiteralPath $first.cache_path -PathType Leaf
    $actual=$(if($exists){[int64](Get-Item -LiteralPath $first.cache_path).Length}else{$null})
    $FixtureReport[$name]=[ordered]@{limit=$script:FixtureLimit;first=(ConvertTo-FixtureSummary $first);
        second=(ConvertTo-FixtureSummary $second);cache_exists=$exists;cache_bytes=$actual}
}
# The already-published seed is one byte over this policy too. Reject it on
# READ, rebuild from the tiny canonical fixture, and decline the oversized write.
$script:FixtureLimit=$size-1
$readOver=Read-BridgeReplyIndex -Path %%LOG%% -CachePath %%SEED%%
$FixtureReport.read_over=ConvertTo-FixtureSummary $readOver
$script:FixtureLimit=[int64]67108864
# A destination directory forces the actual Move/Replace to fail. The
# canonical snapshot remains valid, but cache publication MUST NOT be green.
$target=Join-Path %%CACHE_ROOT%% 'write-failure.json'
$parserKey=(Get-BridgeReplyTextHash ('date-strings-v1:'+$PSVersionTable.PSVersion.ToString())).Substring(0,24).ToLowerInvariant()
$actual=Join-Path (Split-Path -Parent $target) ('write-failure.'+$parserKey+'.json')
[void][IO.Directory]::CreateDirectory($actual)
$failed=Read-BridgeReplyIndex -Path %%LOG%% -CachePath $target
$FixtureReport.write_failure=ConvertTo-FixtureSummary $failed
$FixtureReport.temporary_files=@(Get-ChildItem -LiteralPath %%CACHE_ROOT%% -File -Filter '*.tmp' | ForEach-Object {$_.Name})
""", LOG=log, SEED=root / "cache" / "seed.json", CACHE_ROOT=root / "cache") + REPORT
    done = subprocess.run(_command(ps, root, "serialized-boundaries", script), cwd=root,
                          env=_environment(root), capture_output=True, text=True, timeout=SMALL_TIMEOUT)
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout)


def test_serialized_cache_default_policy_is_explicit_and_bounded(serialized_cache_boundaries):
    assert serialized_cache_boundaries["default_policy"] == 67108864


@pytest.mark.parametrize("case", ["limit_minus_one", "equal", "limit_plus_one"])
def test_serialized_cache_limits_use_actual_utf8_envelope_bytes(serialized_cache_boundaries, case):
    report = serialized_cache_boundaries
    assert report["physical_bytes"] == report["utf8_bytes"] > report["utf16_chars"]
    value = report[case]
    if case == "limit_plus_one":
        assert value["first"]["cache_status"] == "not_cacheable_envelope_exceeds_bounds_snapshot_valid"
        assert value["second"]["cache_status"] == "not_cacheable_envelope_exceeds_bounds_snapshot_valid"
        assert not value["cache_exists"]
        assert value["first"]["parsed_rows"] == value["second"]["parsed_rows"] == 1
    else:
        assert value["cache_exists"] and value["cache_bytes"] <= value["limit"]
        assert value["first"]["cache_status"] == "rebuilt"
        assert value["second"]["cache_status"] == "incremental" and value["second"]["parsed_rows"] == 0
    assert value["first"]["keys"] == value["second"]["keys"]


def test_serialized_cache_read_uses_the_same_envelope_limit(serialized_cache_boundaries):
    value = serialized_cache_boundaries["read_over"]
    assert value["cache_status"] == "not_cacheable_envelope_exceeds_bounds_snapshot_valid"
    assert value["parsed_rows"] == 1 and value["keys"] == [
        {"request_id": "size-boundary", "in_reply_to_request_id": None}]


def test_serialized_cache_write_failure_is_reported_without_losing_the_snapshot(serialized_cache_boundaries):
    report = serialized_cache_boundaries
    assert report["write_failure"]["cache_status"] == "write_failed_snapshot_valid"
    assert report["write_failure"]["parsed_rows"] == 1
    assert report["write_failure"]["keys"] == [{"request_id": "size-boundary", "in_reply_to_request_id": None}]
    assert report["temporary_files"] == []
