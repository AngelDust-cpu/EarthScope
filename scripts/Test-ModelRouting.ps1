param(
    [string]$OpenCode = (Join-Path $PSScriptRoot '..\.tools\opencode\opencode.exe')
)
$ErrorActionPreference = 'Stop'
$expected = @{
    backend = 'opencode/deepseek-v4.1-flash-free'
    data = 'opencode/glm-5.3-flash-free'
    frontend = 'openai/gpt-6.1-sol#medium'
    research = 'openai/gpt-6-luna'
    science = 'nvidia/nvidia/nemotron-3-ultra-550b-a55b'
    qa = 'opencode/inkling-2-small-free'
    pitch = 'openai/gpt-6-luna'
    plan = 'opencode/inkling-2-small-free'
}
$configPath = Join-Path $PSScriptRoot '..\opencode.json'
$config = Get-Content $configPath -Raw | ConvertFrom-Json
if ($config.default_agent -ne 'plan') { throw 'Default must be maintenance Plan' }
if ($config.model -ne $expected.plan) { throw 'Wrong maintenance model' }
if ($config.share -ne 'disabled') { throw 'Sharing unexpectedly enabled' }
$raw = & $OpenCode debug agents --standalone
if ($LASTEXITCODE -ne 0) { throw 'OpenCode agent discovery failed' }
$agents = $raw | ConvertFrom-Json
if (@($agents | Where-Object id -eq 'orchestrator').Count -ne 0) {
    throw 'UNCONFIGURED Orchestrator must be absent from discovered agents'
}
if (@($agents | Where-Object id -eq 'build').Count -ne 0) {
    throw 'Build fallback must be disabled during maintenance'
}
$registryRaw = & $OpenCode api get /api/model
if ($LASTEXITCODE -ne 0) { throw 'Model registry read failed' }
$registry = ($registryRaw | ConvertFrom-Json).data
foreach ($id in ($expected.Keys | Sort-Object)) {
    $matches = @($agents | Where-Object id -eq $id)
    if ($matches.Count -ne 1) { throw "Agent missing or duplicated: $id" }
    $agent = $matches[0]
    $actual = "$($agent.model.providerID)/$($agent.model.id)"
    if ($agent.model.variant) { $actual += "#$($agent.model.variant)" }
    if ($actual -ne $expected[$id]) { throw "Model mismatch for ${id}: $actual" }
    $mode = if ($id -eq 'plan') { 'primary' } else { 'subagent' }
    if ($agent.mode -ne $mode) { throw "Mode mismatch: $id" }
    $route = @($registry | Where-Object {
        $_.providerID -eq $agent.model.providerID -and
        $_.id -eq $agent.model.id -and $_.enabled -ne $false
    })
    if ($route.Count -ne 1) { throw "Route absent or disabled: $actual" }
    if ($id -eq 'plan') {
        foreach ($action in @('edit','shell','subagent','read','webfetch')) {
            $rule = $agent.permissions | Where-Object {
                ($_.action -eq '*' -or $_.action -eq $action) -and $_.resource -eq '*'
            } | Select-Object -Last 1
            if ($rule.effect -ne 'deny') { throw "Maintenance allows $action" }
        }
    }
    Write-Output "PASS $id $actual $mode"
}
$deepseek = @($agents | Where-Object {
    $_.id -in $expected.Keys -and $_.model.id -match 'deepseek'
})
if ($deepseek.Count -gt 2) { throw 'DeepSeek exceeds two agents' }
foreach ($agent in $deepseek) {
    if ($agent.id -notin @('backend','data')) { throw 'DeepSeek assigned outside Backend/Data' }
}
$blocked = Get-Content (Join-Path $PSScriptRoot '..\.opencode\agents\orchestrator.md') -Raw
if ($blocked -notmatch '(?m)^disabled:\s*true\s*$') { throw 'Missing disabled guard' }
if ($blocked -match '(?m)^model:') { throw 'UNCONFIGURED Orchestrator has a model pin' }
Write-Output "PASS $($expected.Count) pins; DeepSeek=$($deepseek.Count); Orchestrator absent; Build disabled; maintenance tools denied."
Write-Output 'Registry/configuration checks only; runtime inference evidence is in MODEL-ROUTING.md.'
