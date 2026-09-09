param([string]$ExecutablePath)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$profileRoot = Join-Path $projectRoot ('build/smoke-appdata-' + [guid]::NewGuid().ToString('N'))
$executable = if ($ExecutablePath) { (Resolve-Path -LiteralPath $ExecutablePath).Path } else { Join-Path $projectRoot 'frontend/release/CoC Field Console 0.1.0.exe' }
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$origin = "http://127.0.0.1:$port/api"
$previousAppData = $env:APPDATA
$previousPort = $env:COC_API_PORT
$previousElectronMode = $env:ELECTRON_RUN_AS_NODE
$env:APPDATA = $profileRoot
$env:COC_API_PORT = [string]$port
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $profileRoot | Out-Null
$process = $null
try {
    # Chromium's Windows app-data lookup does not always honor APPDATA alone.
    $userDataPath = Join-Path $profileRoot 'CoC Field Console'
    $process = Start-Process -FilePath $executable -ArgumentList "--user-data-dir=`"$userDataPath`"" -WindowStyle Hidden -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $health = Invoke-RestMethod "$origin/health" -TimeoutSec 1
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'Portable backend did not become healthy.' }
    $configPath = Join-Path $profileRoot 'CoC Field Console/bot_config.json'
    $disk = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $config = Invoke-RestMethod "$origin/config"
    if ($disk.dryRun -ne $true -or $config.dryRun -ne $true) { throw 'New profile is not dry-run; refusing Start.' }
    Invoke-RestMethod -Method Post "$origin/session/start" | Out-Null
    $telemetry = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        $telemetry = Invoke-RestMethod "$origin/telemetry"
        if ($telemetry.terminalResult) { break }
    }
    Invoke-RestMethod -Method Post "$origin/session/stop" | Out-Null
    if ($telemetry.dryRun -ne $true) { throw 'Started session did not report dry-run.' }
    [pscustomobject]@{
        profile = $profileRoot
        dryRun = $config.dryRun
        farmMode = $config.farmMode
        battlesPerSession = $config.battlesPerSession
        terminalResult = $telemetry.terminalResult
        terminalMessage = $telemetry.terminalMessage
    } | ConvertTo-Json
    Invoke-RestMethod "$origin/logs?limit=100" | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $profileRoot 'smoke-session-logs.json')
} finally {
    # Only close processes launched under this unique profile, including the
    # portable extractor's children. Never close a pre-existing app instance.
    if ($process) {
        $processes = @(Get-CimInstance Win32_Process)
        $ownedIds = @([int]$process.Id)
        do {
            $children = @($processes | Where-Object { $_.ParentProcessId -in $ownedIds -and $_.ProcessId -notin $ownedIds })
            $ownedIds += @($children | ForEach-Object { [int]$_.ProcessId })
        } while ($children.Count -gt 0)
        [array]::Reverse($ownedIds)
        foreach ($ownedId in $ownedIds) { Stop-Process -Id $ownedId -ErrorAction SilentlyContinue }
    }
    $env:APPDATA = $previousAppData
    $env:COC_API_PORT = $previousPort
    $env:ELECTRON_RUN_AS_NODE = $previousElectronMode
}
