$ErrorActionPreference = 'Stop'
$install = Join-Path $env:LOCALAPPDATA 'Programs\Oversight'
$data = Join-Path $env:LOCALAPPDATA 'Oversight'
function Write-SmokeDiagnostic {
    $log = Join-Path $data '.runtime\desktop-session.log'
    if (Test-Path -LiteralPath $log -PathType Leaf) {
        Write-Output 'Installed session diagnostic (sanitized, last 32 lines):'
        try { Get-Content -LiteralPath $log -Tail 32 -ErrorAction Stop }
        catch { Write-Output 'Installed session diagnostic could not be read.' }
    } else {
        Write-Output 'Installed session diagnostic unavailable: failed before log creation.'
    }
}
$installer = @(Get-ChildItem desktop/target/release/bundle/nsis/*-setup.exe)
if ($installer.Count -ne 1) { throw 'Expected exactly one NSIS installer' }
$templates = @(Get-ChildItem desktop/target -Recurse -Filter installer.nsi)
if ($templates.Count -eq 0) { throw 'Generated NSIS template missing' }
foreach ($template in $templates) {
    $script = Get-Content -Raw $template.FullName
    if ($script -notmatch '(?m)^\s*SetCompressor\s+(?:/SOLID\s+)?"?zlib"?\s*$') { throw 'NSIS compressor is not zlib' }
    if ($script -match '(?mi)^\s*SetCompressor\s+.*(?:lzma|bzip2)') { throw 'Forbidden NSIS compressor' }
}
$installed = Start-Process $installer[0].FullName -ArgumentList '/S' -Wait -PassThru
if ($installed.ExitCode -ne 0) { throw "Installer failed: $($installed.ExitCode)" }
$exe = Join-Path $install 'oversight-desktop.exe'
if (!(Test-Path $exe)) { throw 'Installed executable missing at accepted location' }
$git = (Get-Command git.exe -CommandType Application).Source
$originalPath = $env:PATH
try {
    $env:PATH = "$(Split-Path $git);$env:SystemRoot\System32;$env:SystemRoot"
    foreach ($tool in @('python.exe', 'uv.exe', 'node.exe')) {
        if (Get-Command $tool -ErrorAction SilentlyContinue) { throw "Unexpected prerequisite: $tool" }
    }
    foreach ($attempt in 1..2) {
        $started = Start-Process $exe -ArgumentList '--smoke' -PassThru
        if (!$started.WaitForExit(140000)) {
            $started.Kill()
            $started.WaitForExit(10000) | Out-Null
            Write-SmokeDiagnostic
            throw 'Installed smoke exceeded health/close deadline'
        }
        $started.Refresh()
        if ($started.ExitCode -ne 0) {
            Write-SmokeDiagnostic
            throw "Smoke $attempt failed: $($started.ExitCode)"
        }
        # Snapshot by executable ownership, not process names; the Job must drain descendants.
        $survivors = @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($install, [System.StringComparison]::OrdinalIgnoreCase)
        })
        if ($survivors.Count -ne 0) { throw 'Installed payload has surviving owned processes' }
        $listeners = @(Get-NetTCPConnection -LocalPort 8710 -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -ne 0) { throw 'Port 8710 still has a listener' }
        "Installed smoke $attempt`: exit 0, no owned process or listener" | Add-Content package-evidence/smoke.txt
    }
} finally {
    $env:PATH = $originalPath
}
if (!(Test-Path (Join-Path $data '.env'))) { throw 'First-launch configuration missing' }
$marker = Join-Path $data '.runtime\installer-retention-test.txt'
'Preserve retained state on uninstall.' | Set-Content $marker
$uninstaller = Join-Path $install 'uninstall.exe'
$uninstalled = Start-Process $uninstaller -ArgumentList '/S' -PassThru
if (!$uninstalled.WaitForExit(60000) -or $uninstalled.ExitCode -ne 0) { throw 'Uninstall failed' }
# NSIS may copy its uninstaller into TEMP; wait for payload removal, with a deadline.
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while ((Test-Path $exe) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
if (Test-Path $exe) { throw 'Installed executable survived uninstall' }
if (!(Test-Path $marker) -or !(Test-Path (Join-Path $data '.env'))) { throw 'Uninstall removed retained data' }
Get-FileHash $installer[0].FullName -Algorithm SHA256 | Format-List | Out-File package-evidence/installer-sha256.txt
"Installer bytes: $($installer[0].Length)" | Add-Content package-evidence/smoke.txt
