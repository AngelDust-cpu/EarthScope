[CmdletBinding(DefaultParameterSetName = 'List')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Run')]
    [ValidateSet('research','science','data','backend','frontend','qa','pitch')]
    [string]$Role,
    [Parameter(Mandatory = $true, ParameterSetName = 'Run')]
    [ValidateNotNullOrEmpty()]
    [string]$Task,
    [Parameter(ParameterSetName = 'List')]
    [switch]$List,
    [string]$OpenCode
)

$ErrorActionPreference = 'Stop'
if (!$OpenCode) {
    $OpenCode = Get-Process opencode-cli -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Path
}
if (!$OpenCode) {
    $OpenCode = Join-Path $PSScriptRoot '..\.tools\opencode\opencode.exe'
}
$OpenCode = (Resolve-Path -LiteralPath $OpenCode).Path
$roles = @('research','science','data','backend','frontend','qa','pitch')

Push-Location (Join-Path $PSScriptRoot '..')
try {
    $raw = & $OpenCode debug agents --standalone
    if ($LASTEXITCODE -ne 0) { throw 'OpenCode agent discovery failed.' }
    $agents = $raw | ConvertFrom-Json
    $raw = & $OpenCode api get /api/model
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read the active OpenCode model catalog.' }
    $catalog = ($raw | ConvertFrom-Json).data

    $routes = foreach ($name in $roles) {
        $agent = $agents | Where-Object { $_.id -eq $name -and $_.mode -eq 'subagent' }
        $model = $agent.model
        $entry = $catalog | Where-Object {
            $_.providerID -eq $model.providerID -and $_.modelID -eq $model.id -and $_.enabled -eq $true
        } | Select-Object -First 1
        $status = if ($entry) { 'AVAILABLE' } else { 'UNCONFIGURED' }
        $route = if ($model) { "$($model.providerID)/$($model.id)" }
        if ($model.variant) {
            $route += "#$($model.variant)"
            if ($model.variant -notin @($entry.variants | ForEach-Object id)) { $status = 'UNCONFIGURED' }
        }
        [pscustomobject]@{ Role = $name; Model = $route; Status = $status }
    }
    if ($PSCmdlet.ParameterSetName -eq 'List') {
        $routes | Format-Table -AutoSize
        return
    }

    $selected = $routes | Where-Object Role -eq $Role
    if ($selected.Status -ne 'AVAILABLE') { throw "$Role is UNCONFIGURED." }
    Write-Host "OpenCode dispatch: $Role -> $($selected.Model)"
    $cost = [decimal]0
    $steps = 0
    $session = $null
    $runError = $null
    & $OpenCode run --archived --format json --agent $Role --model $selected.Model -- $Task |
        ForEach-Object {
            $event = $_ | ConvertFrom-Json
            if ($event.sessionID) { $session = $event.sessionID }
            switch ($event.type) {
                'text' { Write-Output $event.part.text }
                'step_finish' {
                    if ($null -ne $event.part.cost) {
                        $cost += [decimal]$event.part.cost
                        $steps++
                    }
                }
                'error' { $runError = $event.error.message }
            }
        }
    $runExit = $LASTEXITCODE
    if ($session) { Write-Host "OpenCode session: $session" }
    if ($steps) { Write-Host "OpenCode estimated cost: $cost USD" }
    else { Write-Host 'OpenCode estimated cost: unavailable' }
    if ($runExit -ne 0 -or $runError -or !$steps) {
        throw "OpenCode run failed or incomplete (exit $runExit). $runError"
    }
} finally {
    Pop-Location
}
