param(
    [Parameter(Mandatory = $true)]
    [string[]]$SessionID,
    [string]$OpenCode = (Join-Path $PSScriptRoot '..\.tools\opencode\opencode.exe')
)
$ErrorActionPreference = 'Stop'
# Read only completed sessions. Does not invoke any model or expose reasoning/tool inputs.
$rows = foreach ($id in $SessionID) {
    if ($id -notmatch '^ses_[A-Za-z0-9]+$') { throw 'Invalid session ID' }
    $raw = & $OpenCode api get "/api/session/$id/message"
    if ($LASTEXITCODE -ne 0) { throw "Cannot read session: $id" }
    $messages = ($raw | ConvertFrom-Json).data
    foreach ($message in @($messages | Where-Object type -eq 'assistant')) {
        [pscustomobject]@{
            session = $id
            agent = $message.agent
            provider = $message.model.providerID
            model = $message.model.id
            finish = $message.finish
            cost = $message.cost
            toolParts = @($message.content | Where-Object type -eq 'tool').Count
        }
    }
}
$rows | ConvertTo-Json -Depth 4
