# Windows PowerShell 5.1 + WinForms. Does not launch unguarded MCP.
param([switch]$SmokeTest)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$project = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$state = Join-Path $env:LOCALAPPDATA 'Hermes-OneClick'
$configFile = Join-Path $state 'settings-a4.json'
$browserCandidates = @((Join-Path $env:LOCALAPPDATA 'Yandex\YandexBrowser\Application\browser.exe'))
$browserDefault = ($browserCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1)
if (-not $browserDefault) { $browserDefault = '' }
$f = New-Object Windows.Forms.Form
$f.Text='Hermes — установка и разрешения';$f.Width=680;$f.Height=825
$f.StartPosition='CenterScreen';$f.MaximizeBox=$false;$f.FormBorderStyle='FixedDialog'
# Keep actions reachable on compact 768px Windows screens.
$screen=[Windows.Forms.Screen]::PrimaryScreen
if ($null -ne $screen -and $screen.WorkingArea.Height -gt 500) {
    $f.Height=[Math]::Max(500,[Math]::Min(825,$screen.WorkingArea.Height-55))
}
$f.AutoScroll=$true
$f.AutoScrollMinSize=New-Object Drawing.Size(645,1000)
$f.Font=New-Object Drawing.Font('Segoe UI',10)
function L([string]$v,[int]$y,[int]$height=27) {
    $a=New-Object Windows.Forms.Label
    $a.Text=$v;$a.Left=22;$a.Top=$y;$a.Width=620;$a.Height=$height
    $f.Controls.Add($a);return $a
}
function T([int]$y,[string]$value='') {
    $a=New-Object Windows.Forms.TextBox
    $a.Left=22;$a.Top=$y;$a.Width=615;$a.Text=$value
    $f.Controls.Add($a);return $a
}
$null=L 'Hermes: настройки первого запуска' 15
$null=L 'Права ИИ' 50
$mode=New-Object Windows.Forms.ComboBox
$mode.Left=22;$mode.Top=79;$mode.Width=615;$mode.DropDownStyle='DropDownList'
[void]$mode.Items.Add('Только чтение — никаких изменений')
[void]$mode.Items.Add('Полный доступ — разрешённые изменения в выбранной области')
$mode.SelectedIndex=0;$f.Controls.Add($mode)
$null=L 'Разрешённая папка (область доступа)' 116
$scope=T 145 $project
$null=L 'Яндекс Браузер (browser.exe)' 185
$browser=T 214 $browserDefault
$null=L 'Количество параллельных потоков (1–5)' 253
$workers=New-Object Windows.Forms.NumericUpDown
$workers.Left=22;$workers.Top=281;$workers.Width=100
$workers.Minimum=1;$workers.Maximum=5;$workers.Value=3;$f.Controls.Add($workers)
$null=L 'Telegram Bot Token — необязательно, DPAPI' 320
$telegram=T 349;$telegram.UseSystemPasswordChar=$true
$null=L 'Control Plane API Key — необязательно, DPAPI' 386
$api=T 415;$api.UseSystemPasswordChar=$true
# Only nonsecret settings are restored to the visible form.
if (Test-Path -LiteralPath $configFile -PathType Leaf) {
    try {
        $saved = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($saved.schema_version -eq 1 -and $saved.browser -eq 'yandex') {
            if ($saved.mode -eq 'read_only') { $mode.SelectedIndex=0 }
            elseif ($saved.mode -eq 'full_access') { $mode.SelectedIndex=1 }
            if ($saved.scope -is [string]) { $scope.Text=$saved.scope }
            if ($saved.browser_exe -is [string]) { $browser.Text=$saved.browser_exe }
            if ($saved.workers -ge 1 -and $saved.workers -le 5) {
                $workers.Value=[decimal]$saved.workers
            }
        }
    } catch {
        # Leave safe defaults; corrupted files never trigger a launch.
    }
}
$warning=L 'Выбор прав пока НЕ применяется к действующему MCP. Мастер блокирует запуск до подключения защищённого брокера инструментов.' 456 49
$warning.ForeColor=[Drawing.Color]::DarkRed
$status=L 'Выберите параметры, затем сохраните настройки.' 519 33
function Secret([string]$value,[string]$name) {
    if ([string]::IsNullOrWhiteSpace($value)) { return }
    $secure=ConvertTo-SecureString $value -AsPlainText -Force
    $target=Join-Path $state ($name+'.dpapi.xml')
    $tmp=$target+'.new'
    try {
        $secure | Export-Clixml -LiteralPath $tmp -Force
        if (Test-Path -LiteralPath $target) {
            Copy-Item -LiteralPath $target -Destination ($target+'.previous') -Force
        }
        Move-Item -LiteralPath $tmp -Destination $target -Force
    } finally {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
    }
}
$save=New-Object Windows.Forms.Button
$save.Text='Сохранить';$save.Left=22;$save.Top=557;$save.Width=172;$save.Height=32
$save.Add_Click({
    try {
        if (-not (Test-Path -LiteralPath $scope.Text -PathType Container)) { throw 'Папка не найдена' }
        if (-not (Test-Path -LiteralPath $browser.Text -PathType Leaf)) { throw 'Не найден browser.exe' }
        if ([IO.Path]::GetFileName($browser.Text).ToLowerInvariant() -ne 'browser.exe') { throw 'Нужен browser.exe' }
        if ($mode.SelectedIndex -eq 1) {
            $confirm=[Windows.Forms.MessageBox]::Show('Полный доступ разрешает изменение файлов. Подтвердить?','Hermes','YesNo','Warning')
            if ($confirm -ne 'Yes') {
                $status.Text='Полный доступ не подтверждён. Настройки не изменены.'
                return
            }
        }
        if (-not (Test-Path -LiteralPath $state)) { [void](New-Item -ItemType Directory -Path $state -Force) }
        $choice=if($mode.SelectedIndex -eq 0){'read_only'}else{'full_access'}
        $settings=[ordered]@{
            schema_version=1; mode=$choice
            scope=(Resolve-Path -LiteralPath $scope.Text).Path
            browser='yandex'; browser_exe=(Resolve-Path -LiteralPath $browser.Text).Path
            workers=[int]$workers.Value; enforcement='pending_backend_integration'
        }
        $tmp=$configFile+'.tmp'
        [IO.File]::WriteAllText($tmp,($settings | ConvertTo-Json -Depth 4),[Text.Encoding]::UTF8)
        if (Test-Path -LiteralPath $configFile -PathType Leaf) {
            Copy-Item -LiteralPath $configFile -Destination ($configFile+'.previous') -Force
        }
        Move-Item -LiteralPath $tmp -Destination $configFile -Force
        Secret $telegram.Text 'telegram-token'
        Secret $api.Text 'control-plane-key'
        $telegram.Text='';$api.Text=''
        $status.Text='Сохранено. Для запуска нужен интегрированный защищённый MCP.'
    } catch {
        $status.Text='Не удалось сохранить настройки.'
        [void][Windows.Forms.MessageBox]::Show($_.Exception.Message,'Hermes','OK','Warning')
    }
})
$f.Controls.Add($save)
$check=New-Object Windows.Forms.Button
$check.Text='Диагностика';$check.Left=203;$check.Top=557;$check.Width=145;$check.Height=32
$check.Add_Click({
    try {
        if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
            $status.Text='Сначала сохраните настройки.'
            return
        }
        $python=Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) {
            $status.Text='Python отсутствует. Нужен проверенный runtime.'
            return
        }
        $details = & $python.Source -m quickstart.a4.preflight --project $project --settings $configFile
        if ($LASTEXITCODE -ne 0) { throw 'preflight_failed' }
        $report = $details | Select-Object -Last 1 | ConvertFrom-Json
        if ($report.ok -ne $true) { throw 'preflight_failed' }
        $m = if ($report.services.mcp) { 'OK' } else { 'НЕТ' }
        $t = if ($report.services.tunnel) { 'OK' } else { 'НЕТ' }
        $r = if ($report.services.relay) { 'OK' } else { 'НЕТ' }
        $missing=@($report.source_assets.PSObject.Properties | Where-Object { $_.Value -ne $true } | ForEach-Object { $_.Name })
        if ($missing.Count -gt 0) {
            $status.Text="MCP:$m Tunnel:$t Relay:$r. Нет компонентов: " + ($missing -join ', ')
        } else {
            $status.Text="MCP:$m Tunnel:$t Relay:$r. Защита прав ещё не принята."
        }
    } catch {
        $status.Text='Диагностика не прошла. Проверьте пути и установленные компоненты.'
    }
})
$f.Controls.Add($check)
$open=New-Object Windows.Forms.Button
$open.Text='Расширения';$open.Left=357;$open.Top=557;$open.Width=130;$open.Height=32
$open.Add_Click({
    if (Test-Path -LiteralPath $browser.Text -PathType Leaf) {
        Start-Process -FilePath $browser.Text -ArgumentList 'browser://extensions/'
    }
})
$f.Controls.Add($open)
$quit=New-Object Windows.Forms.Button
$quit.Text='Закрыть';$quit.Left=496;$quit.Top=557;$quit.Width=142;$quit.Height=32
$quit.Add_Click({$f.Close()});$f.Controls.Add($quit)
$recover=New-Object Windows.Forms.Button
$recover.Text='Восстановить предыдущие настройки'
$recover.Left=22;$recover.Top=600;$recover.Width=306;$recover.Height=32
$recover.Add_Click({
    try {
        if (-not (Test-Path -LiteralPath ($configFile+'.previous') -PathType Leaf)) {
            $status.Text='Резервной копии настроек пока нет.'
            return
        }
        $answer=[Windows.Forms.MessageBox]::Show(
            'Восстановить предыдущие настройки? Текущие будут сохранены для отката.',
            'Hermes — восстановление','YesNo','Question')
        if ($answer -ne 'Yes') { return }
        $python=Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw 'Python не найден' }
        $res = & $python.Source -m quickstart.a4.recovery restore --name settings-a4.json --state-dir $state
        if ($LASTEXITCODE -ne 0) { throw 'restore_failed' }
        $result = $res | Select-Object -Last 1 | ConvertFrom-Json
        if ($result.ok -ne $true) { throw 'restore_failed' }
        $status.Text='Настройки восстановлены. Перезапустите мастер, чтобы увидеть изменения.'
    } catch {
        $status.Text='Восстановление не выполнено. Текущие настройки сохранены.'
    }
})
$f.Controls.Add($recover)
$prepare=New-Object Windows.Forms.Button
$prepare.Text='Подготовить защищённый MCP'
$prepare.Left=339;$prepare.Top=600;$prepare.Width=299;$prepare.Height=32
$prepare.Add_Click({
    try {
        if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
            $status.Text='Сохраните настройки перед подготовкой MCP.'
            return
        }
        $python=Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw 'python_missing' }
        $observerConfig=Join-Path $state 'a4-observer-config.json'
        $res=& $python.Source -m quickstart.a4.observer_profile --project $project --settings $configFile --destination $observerConfig
        if ($LASTEXITCODE -ne 0) { throw 'observer_blocked' }
        $report=$res | Select-Object -Last 1 | ConvertFrom-Json
        if ($report.ok -ne $true) { throw 'observer_blocked' }
        $status.Text='Read-only MCP подготовлен. Подключение Tunnel ещё не выполнено.'
    } catch {
        $status.Text='MCP не подготовлен: проверьте режим «Только чтение» и пути.'
    }
})
$f.Controls.Add($prepare)
$runReadonly=New-Object Windows.Forms.Button
$runReadonly.Text='Запустить защищённый MCP (только чтение)'
$runReadonly.Left=22;$runReadonly.Top=639;$runReadonly.Width=616;$runReadonly.Height=33
$runReadonly.Add_Click({
    try {
        if ($mode.SelectedIndex -ne 0) {
            throw 'Для полного доступа нужен отдельный защищённый брокер.'
        }
        if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
            throw 'Сначала сохраните настройки.'
        }
        $saved = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($saved.mode -ne 'read_only' -or $saved.scope -ne $scope.Text -or
            $saved.browser_exe -ne $browser.Text -or $saved.workers -ne [int]$workers.Value) {
            throw 'Изменились настройки. Сначала сохраните текущий выбор.'
        }
        $python=Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw 'Python не установлен.' }
        $tokenFile=Join-Path $state 'observer-token.dpapi.xml'
        $newToken=$false
        if (Test-Path -LiteralPath $tokenFile -PathType Leaf) {
            $secure=Import-Clixml -LiteralPath $tokenFile
            if ($secure -isnot [System.Security.SecureString]) {
                throw 'Повреждён ключ observer. Требуется безопасное восстановление.'
            }
        } else {
            $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
            try {
                $bytes=New-Object byte[] 48
                $rng.GetBytes($bytes)
            } finally { $rng.Dispose() }
            $token=[Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
            $secure=ConvertTo-SecureString $token -AsPlainText -Force
            $newToken=$true
        }
        # Persist the encrypted key *before* launching the server, so a
        # crash between readiness and UI return cannot orphan a live server
        # with an unrecoverable bearer. Never write plaintext to disk.
        if ($newToken) {
            $secure | Export-Clixml -LiteralPath $tokenFile
        }
        $env:LOCAL_MCP_OBSERVER_BEARER=[System.Net.NetworkCredential]::new('', $secure).Password
        try {
            $lines = & $python.Source -m quickstart.a4.observer_runtime start --project $project --settings $configFile --state $state --port 18766
            if ($LASTEXITCODE -ne 0) { throw 'Не прошёл запуск или проверка MCP.' }
            $report=($lines | Select-Object -Last 1) | ConvertFrom-Json
            if ($report.ok -ne $true -or $report.read_only -ne $true -or
                $report.tool_count -ne 6) {
                throw 'Защищённый профиль не подтверждён.'
            }
            $status.Text='Read-only MCP готов на 127.0.0.1:18766; Tunnel отдельно НЕ подключён.'
        } finally {
            Remove-Item Env:LOCAL_MCP_OBSERVER_BEARER -ErrorAction SilentlyContinue
        }
    } catch {
        $status.Text='Защищённый MCP не запущен. Исправьте настройки или диагностику.'
        [void][Windows.Forms.MessageBox]::Show($_.Exception.Message,'Hermes','OK','Warning')
    }
})
$f.Controls.Add($runReadonly)
$installCore=New-Object Windows.Forms.Button
$installCore.Text='Установить ядро Hermes Agent (проверенная версия установщика)'
$installCore.Left=22;$installCore.Top=680;$installCore.Width=616;$installCore.Height=33
$script:CoreInstallProcess=$null
$script:CoreInstallTimer=New-Object Windows.Forms.Timer
$script:CoreInstallTimer.Interval=1500
$script:CoreInstallTimer.Add_Tick({
    if ($null -eq $script:CoreInstallProcess) { return }
    if ($script:CoreInstallProcess.HasExited) {
        $script:CoreInstallTimer.Stop()
        $code=$script:CoreInstallProcess.ExitCode
        $script:CoreInstallProcess.Dispose()
        $script:CoreInstallProcess=$null
        $installCore.Enabled=$true
        if ($code -eq 0) {
            $status.Text='Ядро Hermes установлено. MCP/Tunnel/Relay требуют отдельной проверки.'
        } else {
            $status.Text="Установка завершилась с ошибкой $code. Проверьте логи."
        }
    } else {
        $status.Text='Установка ядра Hermes выполняется. Не запускайте второй установщик.'
    }
})
$installCore.Add_Click({
    try {
        $installer=Join-Path $PSScriptRoot 'vendor\hermes-install.ps1'
        $license=Join-Path $PSScriptRoot 'vendor\LICENSE.NousResearch'
        $expected='31dd1373eae71908cac5326d4ffd8adc85d131ff509916df30b2836e4e231b0f'
        if (-not (Test-Path -LiteralPath $installer -PathType Leaf) -or
            -not (Test-Path -LiteralPath $license -PathType Leaf)) {
            throw 'Нет проверенного установщика Hermes или его лицензии.'
        }
        $actual=(Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw 'Проверка SHA-256 не пройдена. Установка отменена.' }
        $confirmation=[Windows.Forms.MessageBox]::Show(
            'Установить Hermes Agent в профиль пользователя? Будут загружены зависимости из доверенных источников. Яндекс Браузер и доступ ИИ не изменяются.',
            'Hermes — установка','YesNo','Question')
        if ($confirmation -ne 'Yes') { return }
        if (-not (Test-Path -LiteralPath $state)) {
            [void](New-Item -ItemType Directory -Path $state -Force)
        }
        $stamp=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')
        $stdout=Join-Path $state ("core-install-" + $stamp + ".stdout.log")
        $stderr=Join-Path $state ("core-install-" + $stamp + ".stderr.log")
        $argsLine='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $installer + '" -NonInteractive -SkipBrowser -SkipComputerUse -Channel stable'
        $script:CoreInstallProcess=Start-Process -FilePath 'powershell.exe' -ArgumentList $argsLine -WorkingDirectory $project -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $installCore.Enabled=$false
        $status.Text='Установка запущена. Результат появится в этом окне.'
        $script:CoreInstallTimer.Start()
    } catch {
        $status.Text='Установка не запущена. Проверьте контрольную сумму и доступ.'
    }
})
$f.Controls.Add($installCore)
$null=L 'Tunnel ID нового подключения (отдельный от действующего)' 723
$tunnelId=T 752
$connectTunnel=New-Object Windows.Forms.Button
$connectTunnel.Text='Подключить защищённый MCP к отдельному Tunnel'
$connectTunnel.Left=22;$connectTunnel.Top=787;$connectTunnel.Width=616;$connectTunnel.Height=34
$connectTunnel.Add_Click({
    try {
        if ($mode.SelectedIndex -ne 0) {
            throw 'Полный доступ требует доверенного брокера.'
        }
        $keyFile=Join-Path $state 'control-plane-key.dpapi.xml'
        $bearerFile=Join-Path $state 'observer-token.dpapi.xml'
        if (-not (Test-Path -LiteralPath $keyFile -PathType Leaf) -or
            -not (Test-Path -LiteralPath $bearerFile -PathType Leaf)) {
            throw 'Сохраните ключ API и запустите защищённый MCP.'
        }
        if ($tunnelId.Text -notmatch '^tunnel_[A-Za-z0-9_-]{8,128}$') {
            throw 'Укажите отдельный действительный Tunnel ID.'
        }
        $confirmation=[Windows.Forms.MessageBox]::Show(
            'Подтвердить создание отдельного подключения hermes-a4-observer? Действующий local-workspace останется без изменений. Требуется отдельный Tunnel ID.',
            'Hermes — подключение MCP','YesNo','Warning')
        if ($confirmation -ne 'Yes') { return }
        $key=Import-Clixml -LiteralPath $keyFile
        $bearer=Import-Clixml -LiteralPath $bearerFile
        if ($key -isnot [System.Security.SecureString] -or
            $bearer -isnot [System.Security.SecureString]) {
            throw 'Файлы с секретами повреждены.'
        }
        $python=Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw 'Не найден Python.' }
        $env:CONTROL_PLANE_API_KEY=[System.Net.NetworkCredential]::new('', $key).Password
        $env:LOCAL_MCP_OBSERVER_BEARER=[System.Net.NetworkCredential]::new('', $bearer).Password
        try {
            $lines=& $python.Source -m quickstart.a4.observer_tunnel connect --state $state --tunnel-id $tunnelId.Text --approved
            if ($LASTEXITCODE -ne 0) { throw 'connection_unverified' }
            $report=($lines | Select-Object -Last 1) | ConvertFrom-Json
            if ($report.ok -ne $true -or
                $report.local_runtime_ready -ne $true) {
                throw 'connection_unverified'
            }
            $status.Text='Локальный Tunnel готов. Доступ через ChatGPT ещё не проверен.'
        } finally {
            Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
            Remove-Item Env:LOCAL_MCP_OBSERVER_BEARER -ErrorAction SilentlyContinue
        }
    } catch {
        $status.Text='Подключение не подтверждено. Не повторяйте без сверки статуса.'
        [void][Windows.Forms.MessageBox]::Show(
            'Проверьте подключение hermes-a4-observer в панели Tunnel. Повторная отправка заблокирована до сверки состояния.',
            'Hermes','OK','Warning')
    }
})
$f.Controls.Add($connectTunnel)
# Explicit development lane: bypasses ONLY the A4 installer gate.
# No OS/MCP permissions, cloud authentication, or security settings changed.
$devInfo=L 'DEV: текущий MCP сохраняет свои права; выбор прав в этом мастере на него не влияет.' 835 48
$devInfo.ForeColor=[Drawing.Color]::DarkOrange
$devLaunch=New-Object Windows.Forms.Button
$devLaunch.Text='DEV: запустить Hermes без стоп-фактора A4'
$devLaunch.Left=22;$devLaunch.Top=885;$devLaunch.Width=615;$devLaunch.Height=37
$devLaunch.Add_Click({
    try {
        $devCmd=Join-Path $project 'Start-Hermes-DEV.cmd'
        if (-not (Test-Path -LiteralPath $devCmd -PathType Leaf)) {
            throw 'DEV-запускатель не найден.'
        }
        Start-Process -FilePath $devCmd -WorkingDirectory $project
        $status.Text='DEV запуск открыт. Проверьте результат в окне Hermes.'
    } catch {
        $status.Text='DEV запуск не состоялся. Проверьте папку проекта.'
    }
})
$f.Controls.Add($devLaunch)
if ($SmokeTest) {
    if ($f.Controls.Count -lt 15 -or -not $runReadonly.Enabled -or
        -not $save.Enabled -or -not $recover.Enabled -or
        -not $devLaunch.Enabled) {
        throw 'a4_gui_control_construction_failed'
    }
    $f.Dispose()
    Write-Output 'A4_GUI_SMOKE_OK'
    exit 0
}
[void]$f.ShowDialog()
