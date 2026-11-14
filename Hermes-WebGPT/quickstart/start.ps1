[CmdletBinding()]
param([switch]$DryRun,[switch]$ResetKey,[switch]$SkipGateway,
      [switch]$DevMode,[switch]$GateCheckOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Guard = Join-Path $PSScriptRoot "services.py"
$StateDir = Join-Path $env:LOCALAPPDATA "Hermes-OneClick"
$LogDir = Join-Path $StateDir "logs"
$KeyPath = Join-Path $StateDir "control-plane-key.dpapi.xml"
$Started = @()
$Reused = @()
$TelegramInjectedByLauncher = $false
# A control-plane key may arrive from a parent shell. The legacy launcher
# must never let MCP/Relay/Gateway inherit it; only the Tunnel child needs it.
$ProvidedControlPlaneKey = $env:CONTROL_PLANE_API_KEY

function Say([string]$Message) { Write-Host ("[Hermes] " + $Message) }
function Assert-A4StartupMode {
    # User-owned development opt-in: bypass ONLY the A4 installer gate.
    # This switch does not modify Windows permissions, MCP capabilities,
    # cloud grants, or any installed service's authentication policy.
    $A4Settings = Join-Path $StateDir "settings-a4.json"
    if (Test-Path -LiteralPath $A4Settings -PathType Leaf) {
        if (-not $DevMode) {
            throw "A4: режим прав выбран, но защищённый MCP ещё не подключён. Для локальной разработки используйте отдельный DEV-запуск."
        }
        Say "DEV MODE: блокировка A4 снята для этого запуска."
        Say "DEV MODE: выбор «Только чтение»/«Полный доступ» в мастере НЕ ограничивает действующий MCP."
    } elseif ($DevMode) {
        Say "DEV MODE: локальный запуск без требования production-приёмки A4."
    }
}
function Get-Loopback([string]$Url) {
    try {
        $req = [System.Net.HttpWebRequest] [System.Net.WebRequest]::Create($Url)
        $req.Proxy = $null
        $req.Timeout = 2000
        $req.ReadWriteTimeout = 2000
        $resp = $req.GetResponse()
        try {
            $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
            try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        } finally { $resp.Dispose() }
        $body = $body.Trim()
        if ($body.StartsWith("{")) { return ($body | ConvertFrom-Json) }
        return $body
    } catch { return $null }
}
function Field([object]$Object, [string]$Name) {
    if ($null -eq $Object -or $Object -is [string]) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}
function Test-Mcp {
    $h = Get-Loopback "http://127.0.0.1:8765/healthz"
    $healthy = ((Field $h "status") -eq "ok") -or ((Field $h "ok") -eq $true)
    if (-not $healthy) { return $false }
    $tools = Field $h "tools"
    if ($null -eq $tools -or $tools -isnot [int] -or $tools -lt 1) { return $false }
    $server = Field $h "server"
    $name = Field $h "name"
    return (($null -ne $server) -or ($null -ne $name -and
            [string]$name -match "local.workspace"))
}
function Test-Tunnel {
    $h = Get-Loopback "http://127.0.0.1:8080/readyz"
    if ($null -eq $h) { return $false }
    if ($h -is [string]) { return $h -eq "ready" }
    return (((Field $h "ready") -eq $true) -or
            ((Field $h "status") -eq "ready") -or
            ((Field $h "state") -eq "ready"))
}
function Test-Relay {
    $h = Get-Loopback "http://127.0.0.1:18765/health"
    return ((Field $h "service") -eq "hermes-webgpt-relay" -and
            (Field $h "protocol") -eq 1)
}
function Assert-PortFree([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(400)) {
            try {
                $client.EndConnect($async)
                throw ("port_" + $Port + "_occupied_by_unknown_service")
            } catch [System.Net.Sockets.SocketException] { }
        }
    } finally { $client.Dispose() }
}
function Wait-Ready([string]$Name, [scriptblock]$Predicate, [int]$Attempts = 25) {
    for ($i=0; $i -lt $Attempts; $i++) {
        if (& $Predicate) { Say ($Name + ": готов"); return }
        Start-Sleep -Milliseconds 400
    }
    throw ($Name + ": нет health/ready endpoint; проверьте приватные логи")
}
function Get-Python {
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"),
        (Join-Path $Project ".venv\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    $found = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $found) { return $found.Source }
    throw "Python не найден. Требуется проверенный Hermes Python runtime."
}
function Get-McpStart {
    foreach ($candidate in @(
        $env:HERMES_MCP_START_CMD,
        (Join-Path $Project "vendor\local-workspace-mcp\windows\start-server.cmd"),
        (Join-Path $env:USERPROFILE "Downloads\local-workspace-mcp\windows\start-server.cmd")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    throw "MCP не найден: укажите HERMES_MCP_START_CMD или установите local-workspace-mcp."
}
function Get-TunnelExe {
    foreach ($candidate in @(
        $env:HERMES_TUNNEL_EXE,
        (Join-Path $Project "vendor\tunnel-client\tunnel-client.exe"),
        (Join-Path $env:USERPROFILE "Downloads\tunnel-client-v0.0.19-windows-amd64\tunnel-client.exe")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    $found = Get-Command tunnel-client.exe -ErrorAction SilentlyContinue
    if ($null -ne $found) { return $found.Source }
    throw "Tunnel не найден: укажите HERMES_TUNNEL_EXE или установите проверенный tunnel-client."
}
function Launch-Child([string]$Name, [string]$Exe, [string]$Arguments, [string]$WorkDir) {
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffffff")
    $stdout = Join-Path $LogDir ($Name + "_" + $stamp + ".stdout.log")
    $stderr = Join-Path $LogDir ($Name + "_" + $stamp + ".stderr.log")
    $proc = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $WorkDir -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Say ($Name + ": запущен PID=" + $proc.Id)
    return $proc
}
function Invoke-Guard([string]$Name, [string]$PythonExe, [bool]$Apply) {
    $arguments = @($Guard, $Name)
    if ($Apply) { $arguments += "--apply" }
    $output = & $PythonExe @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($Name + ": безопасный запуск заблокирован: " + (($output | Out-String).Trim()))
    }
    $line = [string]($output | Select-Object -Last 1)
    $result = $line | ConvertFrom-Json
    if ($result.ok -ne $true) { throw ($Name + ": " + $line) }
    return $result
}
$LaunchMutex = $null
$MutexOwned = $false
try {
    # DryRun is observational and must remain available while a real
    # OneClick launch holds the exclusive mutex. Real launches still fail
    # closed on contention, preserving duplicate-start protection.
    if ($GateCheckOnly) {
        Assert-A4StartupMode
        Say "A4_GATE_OK: только проверка режима запуска; службы и файлы не изменены."
        exit 0
    }
    if (-not $DryRun) {
        $LaunchMutex = [System.Threading.Mutex]::new($false, "Local\HermesOneClickStartup")
        try { $MutexOwned = $LaunchMutex.WaitOne(0) }
        catch [System.Threading.AbandonedMutexException] { $MutexOwned = $true }
        if (-not $MutexOwned) { throw "Другая копия OneClick уже выполняет запуск; повторный запуск отменён." }
    }
    # A control-plane credential is privileged for Tunnel only. Never
    # expose an inherited parent-shell value to a newly started MCP,
    # Relay, Gateway or diagnostic child process.
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    Say "Проверка окружения и уже работающих сервисов..."
    $mcpReady = Test-Mcp
    $tunnelReady = Test-Tunnel
    $relayReady = Test-Relay
    $pythonExe = Get-Python
    if ($DryRun) {
        Say ("DRY RUN: MCP=" + $mcpReady + "; Tunnel=" + $tunnelReady + "; Relay=" + $relayReady)
        if (-not $mcpReady) {
            try { Say ("MCP найден: " + (Get-McpStart)) } catch { Say $_.Exception.Message }
        }
        if (-not $tunnelReady) {
            try { Say ("Tunnel найден: " + (Get-TunnelExe)) } catch { Say $_.Exception.Message }
        }
        $r = Invoke-Guard "relay" $pythonExe $false
        Say ("Relay: " + ($r.preflight | ConvertTo-Json -Compress))
        if (-not $SkipGateway) {
            try {
                $g = Invoke-Guard "gateway" $pythonExe $false
                Say ("Gateway: " + ($g.preflight | ConvertTo-Json -Compress))
            } catch { Say ("Gateway: " + $_.Exception.Message) }
        }
        Say "DRY RUN завершён. Службы, ключи и файлы не менялись."
        exit 0
    }
    # Production mode keeps the A4 permission boundary requirement;
    # explicit DEV mode bypasses this installer-only gate for local tests.
    Assert-A4StartupMode
    if (-not (Test-Path -LiteralPath $StateDir)) { [void](New-Item -ItemType Directory -Path $StateDir -Force) }
    if (-not (Test-Path -LiteralPath $LogDir)) { [void](New-Item -ItemType Directory -Path $LogDir -Force) }
    if ($ResetKey -and (Test-Path -LiteralPath $KeyPath)) {
        Remove-Item -LiteralPath $KeyPath -Force
        Say "Зашифрованный ключ для текущего пользователя сброшен."
    }
    if ($mcpReady) { $Reused += "MCP"; Say "MCP уже работает." }
    else {
        Assert-PortFree 8765
        $mcp = Get-McpStart
        $cmdargs = '/d /c ""' + $mcp + '""'
        $null = Launch-Child "MCP" $env:ComSpec $cmdargs (Split-Path $mcp -Parent)
        Wait-Ready "MCP" { Test-Mcp }
        $Started += "MCP"
    }
    if ($tunnelReady) { $Reused += "Tunnel"; Say "Tunnel уже работает." }
    else {
        Assert-PortFree 8080
        $tunnel = Get-TunnelExe
        $profile = Join-Path $env:APPDATA "tunnel-client\local-workspace.yaml"
        if (-not (Test-Path -LiteralPath $profile -PathType Leaf)) {
            throw "Профиль local-workspace.yaml отсутствует; нужна одноразовая настройка туннеля."
        }
        $newKey = $null
        if (-not [string]::IsNullOrWhiteSpace($ProvidedControlPlaneKey)) {
            # Restore the supplied value for Tunnel's preflight/child only.
            $env:CONTROL_PLANE_API_KEY=$ProvidedControlPlaneKey
        }
        if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
            if (Test-Path -LiteralPath $KeyPath) {
                $key = Import-Clixml -LiteralPath $KeyPath
                if ($key -isnot [System.Security.SecureString]) {
                    throw "Повреждено DPAPI-хранилище ключа. Запустите с -ResetKey."
                }
                $env:CONTROL_PLANE_API_KEY = [System.Net.NetworkCredential]::new("", $key).Password
            } else {
                $newKey = Read-Host "CONTROL_PLANE_API_KEY (скрытый ввод)" -AsSecureString
                $env:CONTROL_PLANE_API_KEY = [System.Net.NetworkCredential]::new("", $newKey).Password
            }
        }
        if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) { throw "Не указан CONTROL_PLANE_API_KEY" }
        $doctor = (& $tunnel doctor --profile local-workspace --explain 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0 -or $doctor -notmatch 'RESULT ok') {
            throw "Диагностика Tunnel не прошла; проверьте ключ, профиль и сеть. Ключ не записан на диск."
        }
        if ($null -ne $newKey) {
            $newKey | Export-Clixml -LiteralPath $KeyPath
            Say "Ключ сохранён с Windows DPAPI для текущего пользователя."
        }
        $null = Launch-Child "Tunnel" $tunnel "run --profile local-workspace" $Project
        # The Tunnel child inherited its one required key at process start.
        # Drop it BEFORE any subsequent Relay/Gateway process is created.
        Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
        Wait-Ready "Tunnel" { Test-Tunnel }
        $Started += "Tunnel"
    }
    $control = Get-Loopback "http://127.0.0.1:8080/health/control-plane"
    if ($null -eq $control) { Say "ВНИМАНИЕ: control plane недоступен." }
    elseif ((Field $control "reason_code") -eq "network_error" -or (Field $control "status") -eq "degraded") {
        Say "ВНИМАНИЕ: возможна проблема DNS/связи с control plane."
    }
    if ($relayReady) { $Reused += "Relay"; Say "Relay уже работает." }
    else {
        $r = Invoke-Guard "relay" $pythonExe $true
        if (-not (Test-Relay)) { throw "Relay не прошёл финальную health-проверку" }
        $Started += "Relay"
        Say "Relay запущен; неопределённые запросы не переотправлялись."
    }
    if (-not $SkipGateway) {
        # The wizard keeps the bot token under Windows DPAPI, never in a
        # command-line argument or a repository file.
        $telegramSecret = Join-Path $StateDir "telegram-token.dpapi.xml"
        if ([string]::IsNullOrWhiteSpace($env:TELEGRAM_BOT_TOKEN) -and
            (Test-Path -LiteralPath $telegramSecret -PathType Leaf)) {
            $telegramSecure = Import-Clixml -LiteralPath $telegramSecret
            if ($telegramSecure -isnot [System.Security.SecureString]) {
                throw "Повреждено хранилище Telegram Bot Token"
            }
            $env:TELEGRAM_BOT_TOKEN = [System.Net.NetworkCredential]::new("", $telegramSecure).Password
            $TelegramInjectedByLauncher = $true
        }
        $g = Invoke-Guard "gateway" $pythonExe $true
        if ((Field (Field $g "preflight") "action") -eq "already_running") { $Reused += "Gateway" }
        else { $Started += "Gateway" }
        Say "Gateway: процесс проверен. Фактическая доставка Telegram требует E2E."
    }
    Say ("ГОТОВО. Запущено: " + ($Started -join ", ") + "; уже работало: " + ($Reused -join ", "))
    Say "Media Bridge выключен до авторизованного ChatGPT Web E2E."
    Say "Для первого запуска нового профиля нужна установка расширения и вход в ChatGPT Web."
    exit 0
} catch {
    Say ("ОСТАНОВЛЕНО: " + $_.Exception.Message)
    Say "Неопределённые отправки Telegram/ChatGPT автоматически не повторяются."
    exit 2
} finally {
    if ($TelegramInjectedByLauncher) {
        Remove-Item Env:TELEGRAM_BOT_TOKEN -ErrorAction SilentlyContinue
    }
    if ($MutexOwned -and $null -ne $LaunchMutex) {
        try { [void]$LaunchMutex.ReleaseMutex() } catch { }
    }
    if ($null -ne $LaunchMutex) { $LaunchMutex.Dispose() }
}
