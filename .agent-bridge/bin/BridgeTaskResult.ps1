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

function Get-BridgeTaskRequestValidation {
    param($Payload)
    # New writes only: historical records without contracts remain readable.
    Set-StrictMode -Version Latest
    $errors=[Collections.Generic.List[string]]::new()
    $names=@(if ($Payload -is [Collections.IDictionary]) {$Payload.Keys} elseif ($null -ne $Payload) {$Payload.PSObject.Properties.Name})
    $hasContract=$names -ccontains 'result_contract'
    $fields=Get-BridgeResultProperty $Payload 'result_fields'
    $contract=Get-BridgeResultProperty $Payload 'result_contract'
    if (-not $hasContract -and $names -ccontains 'result_fields') {
        if ((Get-BridgeResultProperty $Payload 'schema') -cne 'wd.role-request.v1') {
            $errors.Add('orphan_result_fields_requires_explicit_contract')
        } else {
            $contract=@{schema='wd.task-result-contract.v1';required=$fields;additional_properties=$false}
            $hasContract=$true
        }
    }
    if ($hasContract) {
        if (-not (Test-BridgeResultObject $contract)) { $errors.Add('result_contract_must_be_object') }
        else {
            $keys=@(if ($contract -is [Collections.IDictionary]) {$contract.Keys} else {$contract.PSObject.Properties.Name})
            if (@($keys|Where-Object {$_ -cnotin @('schema','required','types','equals','additional_properties')}).Count) { $errors.Add('unknown_contract_field') }
            if ((Get-BridgeResultProperty $contract 'schema') -cne 'wd.task-result-contract.v1') { $errors.Add('unknown_result_contract') }
            $required=Get-BridgeResultProperty $contract 'required'
            if ($required -isnot [array] -or $required.Count -eq 0 -or $required.Count -gt 256) { $errors.Add('required_must_be_bounded_nonempty_array') }
            $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($name in @($required)) {
                if ($name -isnot [string] -or $name -cnotmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') { $errors.Add('invalid_required_field') }
                elseif (-not $seen.Add($name)) { $errors.Add('duplicate_required_field') }
            }
            foreach ($section in @('types','equals')) {
                if ($keys -cnotcontains $section) { continue }
                $values=Get-BridgeResultProperty $contract $section
                if (-not (Test-BridgeResultObject $values)) { $errors.Add($section+'_must_be_object');continue }
                $valueNames=@(if ($values -is [Collections.IDictionary]) {$values.Keys} else {$values.PSObject.Properties.Name})
                foreach ($name in $valueNames) {
                    if ($required -cnotcontains $name) { $errors.Add($section+'_field_not_required:'+ $name) }
                    if ($section -ceq 'types' -and (Get-BridgeResultProperty $values $name) -cnotin @('string','boolean','integer','number','object','array','null')) { $errors.Add('unknown_result_type:'+ $name) }
                }
            }
            if ($keys -ccontains 'additional_properties' -and (Get-BridgeResultProperty $contract 'additional_properties') -isnot [bool]) { $errors.Add('additional_properties_must_be_boolean') }
            if ($names -ccontains 'result_fields') {
                # Field order is not part of the result contract; duplicates are.
                $fieldSet=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
                $matching=($fields -is [array] -and $fields.Count -eq @($required).Count)
                foreach($field in @($fields)) {
                    if($field -isnot [string] -or -not $fieldSet.Add([string]$field)){$matching=$false}
                }
                if(-not $matching -or -not $fieldSet.SetEquals([string[]]@($required))){$errors.Add('result_fields_contract_mismatch')}
            }
        }
    }
    [pscustomobject]@{schema='wd.request-preflight.v1';valid=($errors.Count -eq 0);contract_present=$hasContract;errors=@($errors);authority_effect='none'}
}

function Get-BridgeTaskResultValidation {
    param($Request,$Payload,[string]$Responder='')
    Set-StrictMode -Version Latest
    $requestPayload=Get-BridgeResultProperty $Request 'payload'
    $contract=Get-BridgeResultProperty $requestPayload 'result_contract'
    if ($null -eq $contract -and (Get-BridgeResultProperty $requestPayload 'schema') -ceq 'wd.role-request.v1') {
        $contract=@{schema='wd.task-result-contract.v1';required=(Get-BridgeResultProperty $requestPayload 'result_fields');additional_properties=$false}
    }
    $errors=[Collections.Generic.List[string]]::new()
    $schemaValid=$null; $contentValid=$null
    $reviewTargets=@(if ($Responder) {$Responder} else {([string](Get-BridgeResultProperty $Request 'to') -split ',')|ForEach-Object {$_.Trim()}})
    $independentReview=((Get-BridgeResultProperty $requestPayload 'role') -ceq 'reviewer' -or
        @($reviewTargets|Where-Object {$_ -cin @('claude-rco-1','claude-rco-2')}).Count -gt 0)
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
        if ($schemaValid -and -not $independentReview -and (Test-BridgeResultObject $equals)) {
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
        reported=$null;errors=@($errors);independent_review=$independentReview;
        content_scope=$(if ($independentReview) {'Requester equals assertions cannot constrain an independent reviewer; content requires independent assessment.'} else {'Only explicit request result_contract.equals assertions; other semantic claims require independent review.'})}
}
