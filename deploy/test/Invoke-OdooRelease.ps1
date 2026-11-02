[CmdletBinding()]
param(
    [ValidateSet('Status','Prepare','Promote','Backup','Recover','Rollback')][string]$Action='Status',
    [string]$Commit,
    [string[]]$Upgrade=@(),
    [string[]]$Install=@(),
    [string[]]$Test=@(),
    [string]$Candidate,
    [string]$AcceptSnapshotRestore,
    [string]$CommandId
)
$ErrorActionPreference='Stop'
$env:PYTHONIOENCODING='utf-8'
$connection=Get-Content -LiteralPath "$PSScriptRoot/connection.local.json" -Raw | ConvertFrom-Json
if ($connection.instance_id -notmatch '^i-[0-9a-f]+$' -or $connection.region -notmatch '^[a-z]{2}-[a-z]+-\d$') { throw 'Invalid saved TEST connection' }
function CheckedModules([string[]]$Names) {
    foreach ($name in $Names) {
        if ($name -notmatch '^[a-z][a-z0-9_]*$' -or $name -in @('all','base')) { throw 'Supply explicit module names; base/all are prohibited.' }
    }
    return ($Names -join ',')
}
if (-not $CommandId) {
    $tokens=@('python3','/opt/odoo20-test/release.py',$Action.ToLowerInvariant())
    if ($Action -eq 'Prepare') {
        if ($Commit -notmatch '^[0-9a-f]{40}$') { throw 'Prepare requires the full Git commit SHA already pushed to GitHub.' }
        $tokens+=@('--commit',$Commit)
        foreach ($pair in @(@('upgrade',$Upgrade),@('install',$Install),@('test',$Test))) {
            $names=CheckedModules $pair[1]
            if ($names) { $tokens+=@('--'+$pair[0],$names) }
        }
    }
    if ($Action -eq 'Promote') {
        if ($Candidate -notmatch '^candidate-[a-z0-9-]+$') { throw 'Promote requires a prepared candidate ID.' }
        $tokens+=$Candidate
    }
    if ($Action -eq 'Rollback') {
        if ($AcceptSnapshotRestore -notmatch '^release-[a-z0-9-]+$') { throw 'Rollback requires the CURRENT release ID to acknowledge restoring older data.' }
        $tokens+=@('--accept-snapshot-restore',$AcceptSnapshotRestore)
    }
    $parameters=@{commands=@(($tokens -join ' '));executionTimeout=@('3600')} | ConvertTo-Json -Compress
    $response=& aws ssm send-command --region $connection.region --instance-ids $connection.instance_id --document-name AWS-RunShellScript --comment "Explicit Odoo TEST $Action" --parameters $parameters --output json
    if ($LASTEXITCODE -ne 0) { throw 'Submission failed; inspect SSM history before any retry.' }
    $parsed=($response -join "`n") | ConvertFrom-Json
    $CommandId=$parsed.Command.CommandId
    if (-not $CommandId) { throw 'Unknown submission outcome; reconcile SSM history instead of repeating it.' }
    New-Item -ItemType Directory -Path "$PSScriptRoot/receipts" -Force | Out-Null
    @{command_id=$CommandId;action=$Action;commit=$Commit;candidate=$Candidate;submitted_utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath "$PSScriptRoot/receipts/$CommandId.json" -Encoding utf8
    Write-Host "Command ID: $CommandId . If interrupted, observe this same ID; do not resubmit."
}
if ($CommandId -notmatch '^[0-9a-f-]{36}$') { throw 'Invalid command ID' }
do {
    Start-Sleep -Seconds 5
    $raw=& aws ssm get-command-invocation --region $connection.region --instance-id $connection.instance_id --command-id $CommandId --output json
    if ($LASTEXITCODE -ne 0) { throw "Observation failed. Resume with -CommandId $CommandId; do not resubmit." }
    $result=($raw -join "`n") | ConvertFrom-Json
    Write-Host $result.Status
} while ($result.Status -in @('Pending','InProgress','Delayed'))
Write-Output $result.StandardOutputContent
if ($result.Status -ne 'Success') { throw "Odoo operation held/failed. Inspect its private logs and transaction. Command ID: $CommandId" }
