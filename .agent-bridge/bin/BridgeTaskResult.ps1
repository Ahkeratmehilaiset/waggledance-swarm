#requires -Version 5.1
# Task-specific checks are distinct from correlation and delivery receipts.
. (Join-Path $PSScriptRoot 'BridgeRequestContract.ps1')

function Get-BridgeResultProperty {
    param($Object,[string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [Collections.IDictionary]) { return ,$Object[$Name] }
    $p=$Object.PSObject.Properties[$Name]
    if ($null -ne $p) { return ,$p.Value }
    return $null
}

function Test-BridgeResultObject {
    param($Value)
    return ($Value -is [Collections.IDictionary] -or $Value -is [pscustomobject])
}

function Get-BridgeTaskResultValidation {
    param($Request,$Payload)
    Set-StrictMode -Version Latest
    $requestPayload=Get-BridgeResultProperty $Request 'payload'
    $contract=Get-BridgeResultProperty $requestPayload 'result_contract'
    if ($null -eq $contract -and (Get-BridgeResultProperty $requestPayload 'schema') -ceq 'wd.role-request.v1') {
        $contract=@{schema='wd.task-result-contract.v1';required=(Get-BridgeResultProperty $requestPayload 'result_fields');additional_properties=$false}
    }
    $errors=[Collections.Generic.List[string]]::new()
    $schemaValid=$null; $contentValid=$null
    if ($null -ne $contract) {
        $schemaValid=$true
        if ((Get-BridgeResultProperty $contract 'schema') -cne 'wd.task-result-contract.v1') { $errors.Add('unknown_result_contract') }
        $result=Get-BridgeResultProperty $Payload 'result'
        if (-not (Test-BridgeResultObject $result)) { $errors.Add('payload.result_must_be_object') }
        $required=Get-BridgeResultProperty $contract 'required'
        $types=Get-BridgeResultProperty $contract 'types'
        $equals=Get-BridgeResultProperty $contract 'equals'
        $additional=Get-BridgeResultProperty $contract 'additional_properties'
        if ($null -eq $required -or $required -isnot [array] -or $required.Count -eq 0) { $errors.Add('required_must_be_nonempty_array') }
        if ($null -ne $types -and -not (Test-BridgeResultObject $types)) { $errors.Add('types_must_be_object') }
        if ($null -ne $equals -and -not (Test-BridgeResultObject $equals)) { $errors.Add('equals_must_be_object') }
        if (Test-BridgeResultObject $result) {
            $names=@(if ($result -is [Collections.IDictionary]) {$result.Keys} else {$result.PSObject.Properties|ForEach-Object {$_.Name}})
            foreach ($name in @($required)) {
                if ($name -isnot [string] -or $name -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') { $errors.Add('invalid_required_field'); continue }
                if ($names -cnotcontains $name) { $errors.Add('missing_result_field:'+ $name) }
            }
            if ($null -ne $additional -and $additional -isnot [bool]) { $errors.Add('additional_properties_must_be_boolean') }
            if ($additional -ceq $false -and @($names|Where-Object {$required -cnotcontains $_}).Count) { $errors.Add('unexpected_result_fields') }
            if (Test-BridgeResultObject $types) {
                $typeNames=@(if ($types -is [Collections.IDictionary]) {$types.Keys} else {$types.PSObject.Properties|ForEach-Object {$_.Name}})
                foreach ($name in $typeNames) {
                    $value=Get-BridgeResultProperty $result $name
                    $valid=switch (Get-BridgeResultProperty $types $name) {
                        'string' { $value -is [string] }
                        'boolean' { $value -is [bool] }
                        'integer' { $value -is [int] -or $value -is [long] }
                        'number' { ($value -is [int] -or $value -is [long] -or $value -is [double] -or $value -is [decimal]) -and -not [double]::IsNaN([double]$value) -and -not [double]::IsInfinity([double]$value) }
                        'object' { Test-BridgeResultObject $value }
                        'array' { $value -is [array] }
                        'null' { $null -eq $value }
                        default { $false }
                    }
                    if ($names -cnotcontains $name -or -not $valid) { $errors.Add('invalid_result_type:'+ $name) }
                }
            }
        }
        $schemaValid=($errors.Count -eq 0)
        if ($schemaValid -and (Test-BridgeResultObject $equals)) {
            $equalNames=@(if ($equals -is [Collections.IDictionary]) {$equals.Keys} else {$equals.PSObject.Properties|ForEach-Object {$_.Name}})
            if ($equalNames.Count -gt 0) {
                $contentValid=$true
                foreach ($name in $equalNames) {
                    if ($names -cnotcontains $name -or (ConvertTo-BridgeContractJson (Get-BridgeResultProperty $result $name)) -cne (ConvertTo-BridgeContractJson (Get-BridgeResultProperty $equals $name))) {
                        $contentValid=$false; $errors.Add('result_assertion_failed:'+ $name)
                    }
                }
            }
        }
    }
    [pscustomobject]@{schema='wd.task-result-validation.v1';schema_valid=$schemaValid;content_valid=$contentValid;
        reported=$null;errors=@($errors);content_scope='Only explicit request result_contract.equals assertions; other semantic claims require independent review.'}
}
