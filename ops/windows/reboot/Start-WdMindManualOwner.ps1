#requires -Version 5.1
<#
.SYNOPSIS
    Manual, opt-in owner control for a pinned Mind bundle.
.DESCRIPTION
    Dot-source this file from the candidate bundle in an interactive operator
    PowerShell session. Start-WdMindManualOwner -Enabled returns a retained
    handle; use Get-WdMindManualOwnerStatus and Stop-WdMindManualOwner in the
    same session. Stop closes the control pipe; poll the status until STOPPED
    confirms cooperative cleanup. No process termination is attempted. An
    uncertain owner remains HOLD. Closing the owning PowerShell session is not
    a verified shutdown.
    The bundled candidate CLI obtains the shared OS reservation itself.
    Live packaging and production startup have not been verified.
    The owner-reservation admin is review-only and still disabled. Its operator
    bootstrap is the independent command below; never dot-source the helper by
    filename for that route. Replace all angle-bracket inputs from approved
    external evidence and run the reviewed command directly in PowerShell.
.EXAMPLE
    . (Join-Path $BundleRoot 'Start-WdMindManualOwner.ps1')
    $owner = Start-WdMindManualOwner -Enabled -BundleRoot $BundleRoot `
        -ExpectedManifestHash $ManifestSha256 -OperatorPolicyPath $PolicyPath `
        -OperatorPolicySha256 $PolicySha256 -OperatorApprovalReferenceSha256 $ApprovalSha256 `
        -BridgePythonPath $BridgePythonPath -BridgePythonSha256 $BridgePythonSha256
    Get-WdMindManualOwnerStatus -Owner $owner
    Stop-WdMindManualOwner -Owner $owner
#>
<# BEGIN-WD-MANUAL-EXTERNAL-BOOTSTRAP
$ErrorActionPreference = 'Stop'
$ExpectedCommit = '<EXACT_COMMIT>'
$ExpectedManifestHash = '<EXACT_MANIFEST_SHA256>'
$BridgePythonPath = '<PINNED_PYTHON_PATH>'
$BridgePythonSha256 = '<PINNED_PYTHON_SHA256>'
$Operation = '<create_or_reset>'
function Assert-WdManualUniqueJson([string] $Text) {
    # Bounded RFC JSON grammar and decoded-key uniqueness are checked before
    # ConvertFrom-Json, which otherwise accepts comments and trailing commas.
    if ($Text.Length -gt 4194304) { throw 'manual_json_size' }
    $state = @{ Text=$Text; Pos=0; Keys=0; Tokens=0 }
    $number = New-Object System.Text.RegularExpressions.Regex ('\G-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
    function Skip-JsonWhitespace($s) {
        while ($s.Pos -lt $s.Text.Length -and $s.Text[$s.Pos] -cin @([char]32,[char]9,[char]10,[char]13)) { $s.Pos++ }
    }
    function Read-JsonString($s, [bool] $key) {
        if ($s.Pos -ge $s.Text.Length -or $s.Text[$s.Pos] -cne [char]34) { throw 'manual_json_invalid' }
        $start = $s.Pos
        $s.Pos++
        $closed = $false
        while ($s.Pos -lt $s.Text.Length) {
            $ch = $s.Text[$s.Pos]
            if ($ch -ceq [char]34) { $s.Pos++; $closed = $true; break }
            if ([int]$ch -lt 32) { throw 'manual_json_invalid' }
            if ($ch -ceq [char]92) {
                $s.Pos++
                if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
                $escape = $s.Text[$s.Pos]
                if ($escape -ceq [char]117) {
                    if ($s.Pos + 4 -ge $s.Text.Length -or
                        $s.Text.Substring($s.Pos + 1,4) -cnotmatch '\A[0-9a-fA-F]{4}\z') { throw 'manual_json_invalid' }
                    $s.Pos += 4
                } elseif ($escape -cnotin @([char]34,[char]92,[char]47,[char]98,[char]102,[char]110,[char]114,[char]116)) {
                    throw 'manual_json_invalid'
                }
            }
            $s.Pos++
        }
        if (-not $closed) { throw 'manual_json_invalid' }
        if ($key) {
            $s.Keys++
            $length = $s.Pos - $start
            if ($s.Keys -gt 16384 -or $length -gt 8192) { throw 'manual_json_size' }
            $value = ConvertFrom-Json -InputObject ('[' + $s.Text.Substring($start,$length) + ']') -ErrorAction Stop
            if ($value -is [array] -and $value.Count -eq 1) { $value = $value[0] }
            if ($value -isnot [string]) { throw 'manual_json_invalid' }
            return $value
        }
    }
    function Read-JsonValue($s, [int] $depth) {
        if ($depth -gt 64) { throw 'manual_json_depth' }
        Skip-JsonWhitespace $s
        if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
        $s.Tokens++
        if ($s.Tokens -gt 262144) { throw 'manual_json_size' }
        $ch = $s.Text[$s.Pos]
        if ($ch -ceq [char]34) { Read-JsonString $s $false; return }
        if ($ch -ceq [char]123) {
            $s.Pos++
            $keys = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
            Skip-JsonWhitespace $s
            if ($s.Pos -lt $s.Text.Length -and $s.Text[$s.Pos] -ceq [char]125) { $s.Pos++; return }
            while ($true) {
                Skip-JsonWhitespace $s
                $key = Read-JsonString $s $true
                if (-not $keys.Add($key)) { throw 'manual_json_duplicate' }
                Skip-JsonWhitespace $s
                if ($s.Pos -ge $s.Text.Length -or $s.Text[$s.Pos] -cne [char]58) { throw 'manual_json_invalid' }
                $s.Pos++
                Read-JsonValue $s ($depth + 1)
                Skip-JsonWhitespace $s
                if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
                if ($s.Text[$s.Pos] -ceq [char]125) { $s.Pos++; return }
                if ($s.Text[$s.Pos] -cne [char]44) { throw 'manual_json_invalid' }
                $s.Pos++
            }
        }
        if ($ch -ceq [char]91) {
            $s.Pos++
            Skip-JsonWhitespace $s
            if ($s.Pos -lt $s.Text.Length -and $s.Text[$s.Pos] -ceq [char]93) { $s.Pos++; return }
            while ($true) {
                Read-JsonValue $s ($depth + 1)
                Skip-JsonWhitespace $s
                if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
                if ($s.Text[$s.Pos] -ceq [char]93) { $s.Pos++; return }
                if ($s.Text[$s.Pos] -cne [char]44) { throw 'manual_json_invalid' }
                $s.Pos++
            }
        }
        foreach ($literal in @('true','false','null')) {
            if ($s.Pos + $literal.Length -le $s.Text.Length -and
                $s.Text.Substring($s.Pos,$literal.Length) -ceq $literal) { $s.Pos += $literal.Length; return }
        }
        $match = $number.Match($s.Text,$s.Pos)
        if (-not $match.Success) { throw 'manual_json_invalid' }
        $s.Pos += $match.Length
    }
    Read-JsonValue $state 0
    Skip-JsonWhitespace $state
    if ($state.Pos -ne $Text.Length) { throw 'manual_json_invalid' }
    [void](ConvertFrom-Json -InputObject $Text -ErrorAction Stop)
}
function Read-BootstrapBounded([string] $Path, [int] $Limit) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'manual_bootstrap_refused' }
        $parent = [IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -ceq $current) { break }
        $current = $parent
    }
    $stream = [IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $Limit) { throw 'manual_bootstrap_refused' }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $stream.Read($bytes,$offset,$bytes.Length-$offset)
            if ($count -le 0) { throw 'manual_bootstrap_refused' }
            $offset += $count
        }
        if ($stream.ReadByte() -ne -1) { throw 'manual_bootstrap_refused' }
        return ,$bytes
    } finally { $stream.Dispose() }
}
if ($ExpectedCommit -cnotmatch '\A[0-9a-f]{40}\z' -or $ExpectedManifestHash -cnotmatch '\A[0-9a-fA-F]{64}\z') { throw 'manual_bootstrap_refused' }
$BundleRoot = Join-Path 'C:\Python\wd-reboot-bundles' $ExpectedCommit
$utf8 = New-Object Text.UTF8Encoding($false,$true)
$pointerText = $utf8.GetString((Read-BootstrapBounded 'C:\Python\WD_REBOOT_STATE_CURRENT.json' 65536)).TrimStart([char]0xFEFF)
Assert-WdManualUniqueJson $pointerText
$pointer = $pointerText | ConvertFrom-Json -ErrorAction Stop
if ($pointer.source_commit -cne $ExpectedCommit -or $pointer.final_commit -cne $ExpectedCommit -or
    -not [string]::Equals([string]$pointer.active_bundle, $BundleRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not [string]::Equals([string]$pointer.manifest_sha256, $ExpectedManifestHash, [StringComparison]::OrdinalIgnoreCase) -or
    -not [string]::Equals([string]$pointer.final_manifest_sha256, $ExpectedManifestHash, [StringComparison]::OrdinalIgnoreCase)) { throw 'manual_bootstrap_refused' }
$manifestBytes = Read-BootstrapBounded (Join-Path $BundleRoot 'deployment-manifest.json') 4194304
$sha = [Security.Cryptography.SHA256]::Create()
try { $manifestHash = [BitConverter]::ToString($sha.ComputeHash($manifestBytes)).Replace('-','') }
finally { $sha.Dispose() }
if ($manifestBytes.Length -gt 4194304 -or $manifestHash -ine $ExpectedManifestHash) { throw 'manual_bootstrap_refused' }
$manifestText = $utf8.GetString($manifestBytes).TrimStart([char]0xFEFF)
Assert-WdManualUniqueJson $manifestText
$manifest = $manifestText | ConvertFrom-Json -ErrorAction Stop
if ($manifest.schema_version -ne 1 -or $manifest.source_commit -cne $ExpectedCommit) { throw 'manual_bootstrap_refused' }
$helperBytes = Read-BootstrapBounded (Join-Path $BundleRoot 'Start-WdMindManualOwner.ps1') 8388608
$sha = [Security.Cryptography.SHA256]::Create()
try { $helperHash = [BitConverter]::ToString($sha.ComputeHash($helperBytes)).Replace('-','') }
finally { $sha.Dispose() }
if ($helperBytes.Length -gt 8388608 -or $helperHash -ine [string]$manifest.files.'Start-WdMindManualOwner.ps1') { throw 'manual_bootstrap_refused' }
. ([scriptblock]::Create($utf8.GetString($helperBytes).TrimStart([char]0xFEFF)))
Invoke-WdMindManualReservationAdmin -Enabled -Operation $Operation -BundleRoot $BundleRoot -ExpectedCommit $ExpectedCommit `
    -ExpectedManifestHash $ExpectedManifestHash -BridgePythonPath $BridgePythonPath -BridgePythonSha256 $BridgePythonSha256
END-WD-MANUAL-EXTERNAL-BOOTSTRAP #>
$script:WdMindManualOwnerHelperText = $MyInvocation.MyCommand.ScriptBlock.Ast.Extent.Text

function Assert-WdManualUniqueJson([string] $Text) {
    # Bounded RFC JSON grammar and decoded-key uniqueness are checked before
    # ConvertFrom-Json, which otherwise accepts comments and trailing commas.
    if ($Text.Length -gt 4194304) { throw 'manual_json_size' }
    $state = @{ Text=$Text; Pos=0; Keys=0; Tokens=0 }
    $number = New-Object System.Text.RegularExpressions.Regex ('\G-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
    function Skip-JsonWhitespace($s) {
        while ($s.Pos -lt $s.Text.Length -and $s.Text[$s.Pos] -cin @([char]32,[char]9,[char]10,[char]13)) { $s.Pos++ }
    }
    function Read-JsonString($s, [bool] $key) {
        if ($s.Pos -ge $s.Text.Length -or $s.Text[$s.Pos] -cne [char]34) { throw 'manual_json_invalid' }
        $start = $s.Pos
        $s.Pos++
        $closed = $false
        while ($s.Pos -lt $s.Text.Length) {
            $ch = $s.Text[$s.Pos]
            if ($ch -ceq [char]34) { $s.Pos++; $closed = $true; break }
            if ([int]$ch -lt 32) { throw 'manual_json_invalid' }
            if ($ch -ceq [char]92) {
                $s.Pos++
                if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
                $escape = $s.Text[$s.Pos]
                if ($escape -ceq [char]117) {
                    if ($s.Pos + 4 -ge $s.Text.Length -or
                        $s.Text.Substring($s.Pos + 1,4) -cnotmatch '\A[0-9a-fA-F]{4}\z') { throw 'manual_json_invalid' }
                    $s.Pos += 4
                } elseif ($escape -cnotin @([char]34,[char]92,[char]47,[char]98,[char]102,[char]110,[char]114,[char]116)) {
                    throw 'manual_json_invalid'
                }
            }
            $s.Pos++
        }
        if (-not $closed) { throw 'manual_json_invalid' }
        if ($key) {
            $s.Keys++
            $length = $s.Pos - $start
            if ($s.Keys -gt 16384 -or $length -gt 8192) { throw 'manual_json_size' }
            $value = ConvertFrom-Json -InputObject ('[' + $s.Text.Substring($start,$length) + ']') -ErrorAction Stop
            if ($value -is [array] -and $value.Count -eq 1) { $value = $value[0] }
            if ($value -isnot [string]) { throw 'manual_json_invalid' }
            return $value
        }
    }
    function Read-JsonValue($s, [int] $depth) {
        if ($depth -gt 64) { throw 'manual_json_depth' }
        Skip-JsonWhitespace $s
        if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
        $s.Tokens++
        if ($s.Tokens -gt 262144) { throw 'manual_json_size' }
        $ch = $s.Text[$s.Pos]
        if ($ch -ceq [char]34) { Read-JsonString $s $false; return }
        if ($ch -ceq [char]123) {
            $s.Pos++
            $keys = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
            Skip-JsonWhitespace $s
            if ($s.Pos -lt $s.Text.Length -and $s.Text[$s.Pos] -ceq [char]125) { $s.Pos++; return }
            while ($true) {
                Skip-JsonWhitespace $s
                $key = Read-JsonString $s $true
                if (-not $keys.Add($key)) { throw 'manual_json_duplicate' }
                Skip-JsonWhitespace $s
                if ($s.Pos -ge $s.Text.Length -or $s.Text[$s.Pos] -cne [char]58) { throw 'manual_json_invalid' }
                $s.Pos++
                Read-JsonValue $s ($depth + 1)
                Skip-JsonWhitespace $s
                if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
                if ($s.Text[$s.Pos] -ceq [char]125) { $s.Pos++; return }
                if ($s.Text[$s.Pos] -cne [char]44) { throw 'manual_json_invalid' }
                $s.Pos++
            }
        }
        if ($ch -ceq [char]91) {
            $s.Pos++
            Skip-JsonWhitespace $s
            if ($s.Pos -lt $s.Text.Length -and $s.Text[$s.Pos] -ceq [char]93) { $s.Pos++; return }
            while ($true) {
                Read-JsonValue $s ($depth + 1)
                Skip-JsonWhitespace $s
                if ($s.Pos -ge $s.Text.Length) { throw 'manual_json_invalid' }
                if ($s.Text[$s.Pos] -ceq [char]93) { $s.Pos++; return }
                if ($s.Text[$s.Pos] -cne [char]44) { throw 'manual_json_invalid' }
                $s.Pos++
            }
        }
        foreach ($literal in @('true','false','null')) {
            if ($s.Pos + $literal.Length -le $s.Text.Length -and
                $s.Text.Substring($s.Pos,$literal.Length) -ceq $literal) { $s.Pos += $literal.Length; return }
        }
        $match = $number.Match($s.Text,$s.Pos)
        if (-not $match.Success) { throw 'manual_json_invalid' }
        $s.Pos += $match.Length
    }
    Read-JsonValue $state 0
    Skip-JsonWhitespace $state
    if ($state.Pos -ne $Text.Length) { throw 'manual_json_invalid' }
    [void](ConvertFrom-Json -InputObject $Text -ErrorAction Stop)
}

function Read-WdMindManualPinnedBytes {
    param([string] $Path, [string] $Sha256, [int] $MaxBytes)
    if (-not [IO.Path]::IsPathRooted($Path) -or $Sha256 -cnotmatch '\A[0-9a-fA-F]{64}\z') { throw 'mind_manual_pin' }
    $pathFull = [IO.Path]::GetFullPath($Path)
    $current = $pathFull
    while ($current) {
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'mind_manual_path' }
        $parent = [IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -ceq $current) { break }
        $current = $parent
    }
    $stream = [IO.File]::Open($pathFull,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $MaxBytes) { throw 'mind_manual_size' }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $n = $stream.Read($bytes,$offset,$bytes.Length-$offset)
            if ($n -le 0) { throw 'mind_manual_read' }
            $offset += $n
        }
        if ($stream.ReadByte() -ne -1) { throw 'mind_manual_changed' }
    } finally { $stream.Dispose() }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($actual -cne $Sha256.ToLowerInvariant()) { throw 'mind_manual_pin' }
    return ,$bytes
}

# Operator-invoked review route only. The packaged admin main remains disabled.
# Python/stdlib closure and same-account tampering remain UNKNOWN.
$script:WdMindAdminVerifiedBytes = @'
import hashlib, hmac, importlib.abc, importlib.util, json, os, pathlib, re, stat, sys

def unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result: raise ValueError('duplicate')
        result[k] = v
    return result

def read(path, digest, limit):
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            getattr(before, 'st_file_attributes', 0) & 0x400): raise ValueError('file')
    with open(path, 'rb') as f:
        opened = os.fstat(f.fileno())
        data = f.read(limit + 1)
        after = os.fstat(f.fileno())
    if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or
        (opened.st_dev, opened.st_ino, opened.st_size) != (after.st_dev, after.st_ino, after.st_size) or
        len(data) > limit or len(data) != opened.st_size or type(digest) is not str or
        not re.fullmatch('[0-9a-fA-F]{64}', digest) or
        not hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest.lower())): raise ValueError('pin')
    return data

try:
    root = pathlib.Path(sys.argv[1])
    expected, operation, commit = sys.argv[2:5]
    pointer_path = pathlib.Path(r'C:\Python\WD_REBOOT_STATE_CURRENT.json')
    bundle_store = pathlib.Path(r'C:\Python\wd-reboot-bundles')
    if (not root.is_absolute() or operation not in ('create', 'reset') or
            not re.fullmatch('[0-9a-f]{40}', commit) or
            os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.abspath(bundle_store / commit))): raise ValueError('input')
    pointer_before = os.lstat(pointer_path)
    if (not stat.S_ISREG(pointer_before.st_mode) or pointer_before.st_nlink != 1 or
            getattr(pointer_before, 'st_file_attributes', 0) & 0x400 or pointer_before.st_size > 65536): raise ValueError('pointer')
    with open(pointer_path, 'rb') as stream:
        pointer_open = os.fstat(stream.fileno())
        pointer_raw = stream.read(65537)
        pointer_after = os.fstat(stream.fileno())
    if ((pointer_before.st_dev, pointer_before.st_ino) != (pointer_open.st_dev, pointer_open.st_ino) or
        (pointer_open.st_dev, pointer_open.st_ino, pointer_open.st_size) != (pointer_after.st_dev, pointer_after.st_ino, pointer_after.st_size) or
        len(pointer_raw) != pointer_open.st_size): raise ValueError('pointer')
    pointer = json.loads(pointer_raw.decode('utf-8-sig'), object_pairs_hook=unique)
    if (type(pointer) is not dict or pointer.get('source_commit') != commit or pointer.get('final_commit') != commit or
        type(pointer.get('manifest_sha256')) is not str or pointer['manifest_sha256'].lower() != expected.lower() or
        type(pointer.get('final_manifest_sha256')) is not str or pointer['final_manifest_sha256'].lower() != expected.lower() or
        type(pointer.get('active_bundle')) is not str or
        os.path.normcase(os.path.abspath(pointer['active_bundle'])) != os.path.normcase(os.path.abspath(root))): raise ValueError('pointer')
    raw = read(root / 'deployment-manifest.json', expected, 4 * 1024 * 1024)
    manifest = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    if type(manifest) is not dict or manifest.get('schema_version') != 1 or manifest.get('source_commit') != commit or type(manifest.get('files')) is not dict: raise ValueError('manifest')
    files = manifest['files']
    definition_raw = read(root / 'bridge-code-files.json', files['bridge-code-files.json'], 4 * 1024 * 1024)
    definition = json.loads(definition_raw.decode('utf-8-sig'), object_pairs_hook=unique)
    paths = definition['python_files']
    if type(paths) is not list or len(paths) != len(set(paths)): raise ValueError('definition')
    blobs = {}
    for relative in paths:
        if type(relative) is not str or not relative.endswith('.py'): continue
        pieces = relative.split('/')
        if pieces[0] not in ('tools', 'waggledance'): continue
        if any(not re.fullmatch('[A-Za-z_][A-Za-z_0-9]*(?:\\.py)?', piece) for piece in pieces): raise ValueError('path')
        path = root / 'tools-bootstrap' / pathlib.PurePosixPath(relative)
        data = read(path, files['tools-bootstrap/' + relative], 8 * 1024 * 1024)
        package = pieces[-1] == '__init__.py'
        name = '.'.join(pieces[:-1] if package else pieces[:-1] + [pieces[-1][:-3]])
        if name in blobs: raise ValueError('collision')
        blobs[name] = (str(path), data, package)
    if 'tools.wd_mind_owner_reservation_admin' not in blobs or not blobs.get('tools', (None,None,False))[2]: raise ValueError('missing')
    if any(k == 'tools' or k.startswith('tools.') or k == 'waggledance' or k.startswith('waggledance.') for k in sys.modules): raise ValueError('loaded')
    class Pinned(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] not in ('tools', 'waggledance'): return None
            if fullname not in blobs: raise ModuleNotFoundError('unlisted package module')
            return importlib.util.spec_from_loader(fullname, self, is_package=blobs[fullname][2])
        def create_module(self, spec): return None
        def exec_module(self, module):
            path, data, package = blobs[module.__name__]
            module.__file__ = path
            if package: module.__path__ = []
            exec(compile(data, path, 'exec'), module.__dict__)
    sys.meta_path.insert(0, Pinned())
    if pointer_path.read_bytes() != pointer_raw: raise ValueError('pointer changed')
    from tools import wd_mind_owner_reservation_admin as admin
    code = admin.main([operation, '--bundle-root', str(root), '--manifest-sha256', expected])
    if type(code) is not int: raise ValueError('exit')
except Exception:
    raise SystemExit(2) from None
raise SystemExit(code)
'@

function Invoke-WdMindManualReservationAdmin {
    <# Review-only; no lane entrypoint, no live marker mutation. #>
    [CmdletBinding()]
    param([switch] $Enabled, [Parameter(Mandatory)][ValidateSet('create','reset')][string] $Operation,
          [Parameter(Mandatory)][string] $BundleRoot, [Parameter(Mandatory)][string] $ExpectedManifestHash,
          [Parameter(Mandatory)][string] $ExpectedCommit,
          [Parameter(Mandatory)][string] $BridgePythonPath, [Parameter(Mandatory)][string] $BridgePythonSha256)
    if (-not $Enabled) { throw 'mind_manual_admin_disabled' }
    try {
        if ($ExpectedCommit -cnotmatch '\A[0-9a-f]{40}\z') { throw 'commit' }
        $root = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\\')
        $expectedRoot = Join-Path 'C:\Python\wd-reboot-bundles' $ExpectedCommit
        if (-not [string]::Equals($root, $expectedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'bundle' }
        $pointerPath = 'C:\Python\WD_REBOOT_STATE_CURRENT.json'
        $pointerStream = [IO.File]::Open($pointerPath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($pointerStream.Length -lt 1 -or $pointerStream.Length -gt 65536) { throw 'pointer_size' }
            $firstPointer = New-Object byte[] ([int]$pointerStream.Length)
            $offset = 0
            while ($offset -lt $firstPointer.Length) {
                $count = $pointerStream.Read($firstPointer,$offset,$firstPointer.Length-$offset)
                if ($count -le 0) { throw 'pointer_read' }
                $offset += $count
            }
            if ($pointerStream.ReadByte() -ne -1) { throw 'pointer_changed' }
        } finally { $pointerStream.Dispose() }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $pointerHash = [BitConverter]::ToString($sha.ComputeHash($firstPointer)).Replace('-','') }
        finally { $sha.Dispose() }
        $pointerBytes = Read-WdMindManualPinnedBytes $pointerPath $pointerHash 65536
        $pointerText = [Text.Encoding]::UTF8.GetString($pointerBytes).TrimStart([char]0xFEFF)
        Assert-WdManualUniqueJson $pointerText
        $pointer = $pointerText | ConvertFrom-Json -ErrorAction Stop
        if ($pointer.source_commit -cne $ExpectedCommit -or $pointer.final_commit -cne $ExpectedCommit -or
            -not [string]::Equals([string]$pointer.active_bundle, $expectedRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals([string]$pointer.manifest_sha256, $ExpectedManifestHash, [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals([string]$pointer.final_manifest_sha256, $ExpectedManifestHash, [StringComparison]::OrdinalIgnoreCase)) { throw 'pointer' }
        $manifestPath = Join-Path $root 'deployment-manifest.json'
        $utf8 = New-Object Text.UTF8Encoding($false,$true)
        $manifestText = $utf8.GetString((Read-WdMindManualPinnedBytes $manifestPath $ExpectedManifestHash 4194304)).TrimStart([char]0xFEFF)
        Assert-WdManualUniqueJson $manifestText
        $manifest = $manifestText | ConvertFrom-Json -ErrorAction Stop
        if ($manifest.schema_version -ne 1 -or $manifest.source_commit -cne $ExpectedCommit -or $null -eq $manifest.files) { throw 'manifest' }
        foreach ($name in @('Start-WdMindManualOwner.ps1','BridgeCodeContext.ps1','bridge-code-files.json')) {
            $pin = $manifest.files.PSObject.Properties[$name]
            if ($null -eq $pin -or $pin.Value -isnot [string]) { throw 'pin' }
            $bytes = Read-WdMindManualPinnedBytes (Join-Path $root $name) $pin.Value 8388608
            if ($name -ceq 'Start-WdMindManualOwner.ps1' -and
                $utf8.GetString($bytes).TrimStart([char]0xFEFF) -cne $script:WdMindManualOwnerHelperText) { throw 'helper' }
            if ($name -ceq 'BridgeCodeContext.ps1') { $contextBytes = $bytes }
        }
        [void](Read-WdMindManualPinnedBytes $BridgePythonPath $BridgePythonSha256 134217728)
        . ([scriptblock]::Create($utf8.GetString($contextBytes).TrimStart([char]0xFEFF)))
        $package = Get-WdBridgeCodePackageDefinition -Path (Join-Path $root 'bridge-code-files.json')
        [void](Assert-WdBridgeCodePackageIntegrity -BundleRoot $root -Deployment $manifest -Definition $package.Definition)
        [void](Read-WdMindManualPinnedBytes $pointerPath $pointerHash 65536)
        & $BridgePythonPath -I -S -B -c $script:WdMindAdminVerifiedBytes $root $ExpectedManifestHash $Operation $ExpectedCommit
        return [int]$LASTEXITCODE
    } catch { throw 'mind_manual_admin_refused' }
}

function Get-WdMindManualOwnerRecord {
    param([Parameter(Mandatory)] $Owner)
    if ($null -eq $Owner -or $Owner.Token -cnotmatch '\A[0-9a-f]{32}\z' -or
        -not (Test-Path variable:global:WdMindManualOwnerRecords) -or
        -not $global:WdMindManualOwnerRecords.ContainsKey([string]$Owner.Token)) { throw 'mind_manual_owner_refused' }
    return $global:WdMindManualOwnerRecords[[string]$Owner.Token]
}

function Update-WdMindManualOwner {
    param([Parameter(Mandatory)] $Record)
    try {
        foreach ($which in @('Out','Err')) {
            $task=$Record[($which+'Read')]
            if ($null -eq $task -or -not $task.IsCompleted) { continue }
            $count=$task.GetAwaiter().GetResult()
            if ($count -eq 0) { $Record[($which+'Done')]=$true; $Record[($which+'Read')]=$null; continue }
            if ($which -ceq 'Err') { throw 'mind_manual_stderr' }
            $buffer=$Record.OutBuffer
            for($i=0;$i -lt $count;$i++) {
                if ($buffer[$i] -eq 10) {
                    if ($Record.Line.Length -eq 0 -or $Record.Line.Length -gt 1024) { throw 'mind_manual_status' }
                    $lineBytes=$Record.Line.ToArray()
                    $lineLength=$lineBytes.Length
                    if($lineLength -gt 0 -and $lineBytes[$lineLength-1] -eq 13){$lineLength--}
                    $line=[Text.Encoding]::ASCII.GetString($lineBytes,0,$lineLength)
                    $Record.Line.SetLength(0)
                    $matched=$false
                    foreach($state in @('STARTING','READY','HOLD','STOPPED')) {
                        $expected='{"authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED",'+
                            '"manifest_sha256":"'+$Record.Manifest+'","schema":"wd.mind-local-owner-status.v1",'+
                            '"source_authentication":"UNKNOWN","state":"'+$state+'"}'
                        if($line -ceq $expected){$Record.ReportedState=$state;$matched=$true;break}
                    }
                    if (-not $matched -and $Record.ContainsKey('DiagnosticsEnabled') -and $Record.DiagnosticsEnabled -and $Record.ReportedState -ceq 'HOLD') {
                        $reasons=@('BINDING_CHECK','CACHE_PREFLIGHT','WORKER_SETUP','HANDLER_BUILD','CACHE_RESULT',
                            'FINAL_BINDING_CHECK','SOURCE_PREWARM','SOURCE_BUDGET','ADMISSION','ADMISSION_MISSING','ADMISSION_RESERVED','ADMISSION_LOCK_UNAVAILABLE','POLICY','RUNTIME_BINDING','WORKER_FACTORY',
                            'OWNER_CREATE','EXTERNAL_POLICY','OWNER_START','OWNER_NOT_READY','OWNER_ENTRY','IMPORT','THREAD_START','UNKNOWN')
                        $types=@('ValueError','TypeError','RuntimeError','OSError','FileNotFoundError','PermissionError',
                            'TimeoutError','KeyboardInterrupt','GeneratorExit','CancelledError','FullRuntimeRefused',
                            'FullRuntimeCancellationUnknown','BaseExceptionGroup','ExceptionGroup','LaunchRefused','UNKNOWN')
                        foreach ($reason in $reasons) {
                            foreach ($exceptionType in $types) {
                                $expected='{"authority":"none","exception_type":"'+$exceptionType+'","reason":"'+$reason+
                                    '","schema":"wd.mind-local-owner-start-diagnostic.v1"}'
                                if ($line -ceq $expected -and [int]$Record.DiagnosticCount -lt 2) {
                                    $Record.StartDiagnostic=[pscustomobject]@{schema='wd.mind-local-owner-start-diagnostic.v1';
                                        reason=$reason;exception_type=$exceptionType;authority='none'}
                                    $Record.DiagnosticCount=[int]$Record.DiagnosticCount+1
                                    $matched=$true;break
                                }
                            }
                            if ($matched) { break }
                        }
                        if (-not $matched -and $Record.ContainsKey('WorkerDiagnosticsEnabled') -and $Record.WorkerDiagnosticsEnabled) {
                            $hints=@('arguments','model_pin','offline_environment','reader_mutex','library_import','model_load','serving','UNKNOWN')
                            $codeText=$null
                            try {
                                $decoded=$line | ConvertFrom-Json -ErrorAction Stop
                                $code=$decoded.worker_exit_code
                                if($null -eq $code) {$codeText='null'}
                                elseif(($code -is [int] -or $code -is [long]) -and $code -ge 0 -and $code -le 4294967295) {
                                    $codeText=([long]$code).ToString([Globalization.CultureInfo]::InvariantCulture)
                                }
                            } catch { $codeText=$null }
                            foreach ($reason in $reasons) {
                                foreach ($exceptionType in $types) {
                                    foreach ($hint in $hints) {
                                        if ($null -eq $codeText -or ($hint -cne 'UNKNOWN' -and $codeText -cne 'null') -or
                                            ($hint -ceq 'UNKNOWN' -and $codeText -cne 'null' -and $code -in @(20,21,22,23,24,25,29))) {continue}
                                        $expected='{"authority":"none","exception_type":"'+$exceptionType+'","reason":"'+$reason+
                                            '","schema":"wd.mind-local-owner-start-diagnostic.v2","worker_exit_code":'+$codeText+
                                            ',"worker_exit_stage_hint":"'+$hint+'"}'
                                        if ($line -ceq $expected -and [int]$Record.DiagnosticCount -lt 2) {
                                            $Record.StartDiagnostic=[pscustomobject]@{schema='wd.mind-local-owner-start-diagnostic.v2';
                                                reason=$reason;exception_type=$exceptionType;worker_exit_stage_hint=$hint;
                                                worker_exit_code=$code;authority='none'}
                                            $Record.DiagnosticCount=[int]$Record.DiagnosticCount+1
                                            $matched=$true;break
                                        }
                                    }
                                    if ($matched) { break }
                                }
                                if ($matched) { break }
                            }
                        }
                    }
                    if(-not $matched){throw 'mind_manual_status'}
                } else {
                    if($buffer[$i] -gt 127 -or $Record.Line.Length -ge 1024){throw 'mind_manual_status'}
                    $Record.Line.WriteByte($buffer[$i])
                }
            }
            if($which -ceq 'Out') {
                $Record.OutRead=$Record.Process.StandardOutput.BaseStream.ReadAsync($Record.OutBuffer,0,$Record.OutBuffer.Length)
            } else {
                $Record.ErrRead=$Record.Process.StandardError.BaseStream.ReadAsync($Record.ErrBuffer,0,$Record.ErrBuffer.Length)
            }
        }
        if($Record.Process.HasExited) {
            if($Record.Process.ExitCode -eq 0 -and $Record.OutDone -and $Record.ErrDone -and
               $Record.Line.Length -eq 0 -and $Record.ReportedState -ceq 'STOPPED') {$Record.State='STOPPED'}
            elseif($Record.OutDone -and $Record.ErrDone) {$Record.State='FAILED'}
            else {$Record.State='HOLD'}
        } elseif($Record.Stopping) {$Record.State='HOLD'}
        elseif($Record.ReportedState -ceq 'READY') {$Record.State='READY'}
        elseif($Record.ReportedState -ceq 'HOLD' -or $Record.ReportedState -ceq 'STOPPED') {$Record.State='HOLD'}
        else {$Record.State='STARTING'}
    } catch { $Record.State='HOLD'; $Record.Invalid=$true }
}

function Start-WdMindManualOwner {
    <# Opt-in persistent owner. The creating PowerShell session owns the process;
       closing that session is not claimed to preserve it. No lane identity is authenticated. #>
    [CmdletBinding()]
    param([switch] $Enabled, [Parameter(Mandatory)][string] $BundleRoot, [Parameter(Mandatory)][string] $ExpectedManifestHash,
          [Parameter(Mandatory)][string] $OperatorPolicyPath, [Parameter(Mandatory)][string] $OperatorPolicySha256,
          [Parameter(Mandatory)][string] $OperatorApprovalReferenceSha256,
          [Parameter(Mandatory)][string] $BridgePythonPath,
          [Parameter(Mandatory)][string] $BridgePythonSha256, [switch] $IncludeStartupDiagnostics,
          [switch] $IncludeWorkerStageDiagnostics)
    if (-not $Enabled) { throw 'mind_manual_owner_disabled' }
    if ($IncludeStartupDiagnostics -and $IncludeWorkerStageDiagnostics) { throw 'mind_manual_diagnostics_option' }
    $policy=$null; $record=$null
    try {
        if(-not [IO.Path]::IsPathRooted($BundleRoot) -or $BundleRoot -match '(^|[\\/])\.\.([\\/]|$)' -or
           $OperatorApprovalReferenceSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
           $BridgePythonSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z'){throw 'input'}
        $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
        $manifestPath=Join-Path $root 'deployment-manifest.json'
        $manifestBytes=Read-WdMindManualPinnedBytes $manifestPath $ExpectedManifestHash 4194304
        $utf8=New-Object Text.UTF8Encoding($false,$true)
        $manifest=$utf8.GetString($manifestBytes).TrimStart([char]0xFEFF)|ConvertFrom-Json -ErrorAction Stop
        if($manifest.schema_version -ne 1 -or $null -eq $manifest.files){throw 'manifest'}
        $descriptorName='wd-mind-identifier-policy-approval.json'
        $descriptorPin=$manifest.files.PSObject.Properties[$descriptorName]
        if($null -eq $descriptorPin -or $descriptorPin.Value -isnot [string]){throw 'descriptor'}
        $descriptor=Read-WdMindManualPinnedBytes (Join-Path $root $descriptorName) $descriptorPin.Value 65536
        $expected='{"approval_reference_sha256":"'+$OperatorApprovalReferenceSha256.ToLowerInvariant()+'",'+
            '"identifier_policy_sha256":"'+$OperatorPolicySha256.ToLowerInvariant()+'",'+
            '"schema":"wd.mind-identifier-policy-approval.v1"}'
        if($utf8.GetString($descriptor) -cne $expected){throw 'descriptor'}
        $policy=Read-WdMindManualPinnedBytes $OperatorPolicyPath $OperatorPolicySha256 65536
        [void](Read-WdMindManualPinnedBytes $BridgePythonPath $BridgePythonSha256 134217728)
        $wrapper=Join-Path $root 'Invoke-WdBridgePython.ps1'
        $helperBytes=$null
        foreach($relative in @('Invoke-WdBridgePython.ps1','Start-WdMindManualOwner.ps1',
                                'tools-bootstrap/tools/wd_mind_runtime_child.py')) {
            $pin=$manifest.files.PSObject.Properties[$relative]
            if($null -eq $pin -or $pin.Value -isnot [string]){throw 'entry'}
            $entryBytes=Read-WdMindManualPinnedBytes (Join-Path $root $relative) $pin.Value 8388608
            if($relative -ceq 'Start-WdMindManualOwner.ps1'){$helperBytes=$entryBytes}
        }
        if($null -eq $helperBytes -or $utf8.GetString($helperBytes).TrimStart([char]0xFEFF) -cne
           $script:WdMindManualOwnerHelperText){throw 'helper_source'}
        [void](Read-WdMindManualPinnedBytes $manifestPath $ExpectedManifestHash 4194304)
        [void](Read-WdMindManualPinnedBytes (Join-Path $root $descriptorName) $descriptorPin.Value 65536)
        $wrapperPin=[string]$manifest.files.PSObject.Properties['Invoke-WdBridgePython.ps1'].Value
        $quote={param([string]$v) "'"+$v.Replace("'","''")+"'"}
        $command='$ErrorActionPreference="Stop"; $ProgressPreference="SilentlyContinue"; $f=[IO.File]::OpenRead('+(& $quote $wrapper)+
            ');$s=[Security.Cryptography.SHA256]::Create();try{$h=[BitConverter]::ToString($s.ComputeHash($f)).Replace("-","").ToLowerInvariant()}finally{$f.Dispose();$s.Dispose()}; if($h -cne '+
            (& $quote $wrapperPin.ToLowerInvariant())+' ){exit 2}; & '+(& $quote $wrapper)+
            ' tools/wd_mind_runtime_child.py -VerifyPackage --mind-local-candidate-owner --policy-path '+
            (& $quote ([IO.Path]::GetFullPath($OperatorPolicyPath)))+' --policy-sha256 '+
            (& $quote $OperatorPolicySha256.ToLowerInvariant())+' --approval-reference-sha256 '+
            (& $quote $OperatorApprovalReferenceSha256.ToLowerInvariant())+$(if($IncludeWorkerStageDiagnostics){' --start-diagnostics-v2'}elseif($IncludeStartupDiagnostics){' --start-diagnostics'}else{''})+'; exit $LASTEXITCODE'
        $info=New-Object Diagnostics.ProcessStartInfo
        $shell=if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'}
        $info.FileName=Join-Path $PSHOME $shell
        $info.Arguments='-NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $info.WorkingDirectory=$root; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
        $info.RedirectStandardInput=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
        $environmentAllow=@('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP',
                            'USERPROFILE','LOCALAPPDATA','APPDATA')
        foreach($name in @($info.EnvironmentVariables.Keys)) {
            if($environmentAllow -cnotcontains ([string]$name).ToUpperInvariant()){
                $info.EnvironmentVariables.Remove([string]$name)
            }
        }
        $info.EnvironmentVariables['PSModulePath']=(Join-Path $PSHOME 'Modules')+';'+
            (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules')
        $info.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$ExpectedManifestHash.ToUpperInvariant()
        # Explicit caller-provided handshake measurement; not a manifest-approved interpreter identity.
        $info.EnvironmentVariables['WD_BRIDGE_PYTHON_SHA256']=$BridgePythonSha256.ToUpperInvariant()
        $info.EnvironmentVariables['WD_BRIDGE_PYTHON']=[IO.Path]::GetFullPath($BridgePythonPath)
        if(-not (Test-Path variable:global:WdMindManualOwnerRecords)){$global:WdMindManualOwnerRecords=@{}}
        $token=[Guid]::NewGuid().ToString('N')
        $proc=New-Object Diagnostics.Process; $proc.StartInfo=$info
        $record=@{Process=$proc;Manifest=$ExpectedManifestHash.ToLowerInvariant();State='HOLD';ReportedState='';
            Stopping=$false;Invalid=$false;DiagnosticsEnabled=[bool]($IncludeStartupDiagnostics -or $IncludeWorkerStageDiagnostics);
            WorkerDiagnosticsEnabled=[bool]$IncludeWorkerStageDiagnostics;DiagnosticCount=0;StartDiagnostic=$null;
            OutBuffer=(New-Object byte[] 4096);ErrBuffer=(New-Object byte[] 4096);Line=(New-Object IO.MemoryStream);
            OutRead=$null;ErrRead=$null;OutDone=$false;ErrDone=$false}
        $global:WdMindManualOwnerRecords[$token]=$record
        $handle=[pscustomobject]@{schema='wd.mind-manual-owner-handle.v1';Token=$token;authority='none'}
        if(-not $proc.Start()){return $handle}
        $record.OutRead=$proc.StandardOutput.BaseStream.ReadAsync($record.OutBuffer,0,$record.OutBuffer.Length)
        $record.ErrRead=$proc.StandardError.BaseStream.ReadAsync($record.ErrBuffer,0,$record.ErrBuffer.Length)
        $record.State='STARTING'
        return $handle
    } catch {
        if($null -ne $record){return $handle}
        throw 'mind_manual_owner_refused'
    } finally {
        if($null -ne $policy){[Array]::Clear($policy,0,$policy.Length)}
    }
}

function Get-WdMindManualOwnerStatus {
    [CmdletBinding()] param([Parameter(Mandatory)] $Owner)
    $record=Get-WdMindManualOwnerRecord $Owner
    if(-not $record.Invalid){Update-WdMindManualOwner $record}
    else {
        try { $record.State=if($record.Process.HasExited){'FAILED'}else{'HOLD'} }
        catch { $record.State='HOLD' }
    }
    return [pscustomobject]@{schema='wd.mind-manual-owner-status.v1';state=$record.State;
        authority='none';caller_authentication='UNKNOWN';index_refresh='NOT_SCHEDULED'}
}

function Get-WdMindManualOwnerStartDiagnostic {
    [CmdletBinding()] param([Parameter(Mandatory)] $Owner)
    $record=Get-WdMindManualOwnerRecord $Owner
    if(-not $record.ContainsKey('DiagnosticsEnabled') -or -not $record.DiagnosticsEnabled){throw 'mind_manual_diagnostics_disabled'}
    if(-not $record.Invalid){Update-WdMindManualOwner $record}
    if($record.Invalid){throw 'mind_manual_status'}
    return $record.StartDiagnostic
}

function Stop-WdMindManualOwner {
    [CmdletBinding()] param([Parameter(Mandatory)] $Owner)
    $record=Get-WdMindManualOwnerRecord $Owner
    if(-not $record.Invalid){Update-WdMindManualOwner $record}
    if($record.State -in @('STOPPED','FAILED')){return Get-WdMindManualOwnerStatus $Owner}
    if(-not $record.Stopping -and -not $record.Process.HasExited) {
        try {
            $record.Stopping=$true
            $record.Process.StandardInput.Close()
        } catch { $record.Invalid=$true; $record.State='HOLD' }
    }
    return Get-WdMindManualOwnerStatus $Owner
}

