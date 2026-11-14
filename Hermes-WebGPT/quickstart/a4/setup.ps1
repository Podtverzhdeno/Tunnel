# Windows PowerShell 5.1 + WinForms. Does not launch unguarded MCP.
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
$f.Text='Hermes — установка и разрешения';$f.Width=680;$f.Height=700
$f.StartPosition='CenterScreen';$f.MaximizeBox=$false;$f.FormBorderStyle='FixedDialog'
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
            if ($confirm -ne 'Yes') { throw 'Доступ не подтверждён' }
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
        $status.Text="MCP: $m | Tunnel: $t | Relay: $r. Защита прав: ожидает интеграции."
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
[void]$f.ShowDialog()
