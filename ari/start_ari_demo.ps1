# Start the complete ARI demo from Windows.
#
# This script starts (or reuses) the RoboBOND proxy and ROS social sidecar on
# ARI, verifies both services, and runs the Windows microphone/UI process.
# Ctrl+C stops the local demo and then shuts down both remote processes.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $DemoArguments
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Adjustable connection and remote settings.
# ---------------------------------------------------------------------------
$AriHost = "10.96.21.217"
$AriUser = "pal"
# Intentionally stored in plain text so the physical demo needs no SSH keys or
# repeated password prompts.
$SshPassword = "pal"

$RoboBondPort = 7342
$SocialControlPort = 7343
$UiPort = 8765
$AriLanguage = "nl_NL"
$MicrophoneDevice = 3

$RemoteVenv = "/home/pal/robond-venv"
$RemoteSidecar = "/home/pal/ari_social_proxy.py"
$RemoteRoboBondLog = "/tmp/ari-robond-proxy.log"
$RemoteSidecarLog = "/tmp/ari-social-proxy.log"
$ExpectedSidecarVersion = "ari-social-v0.4.1-operator-emotes"

$SshConnectTimeoutSeconds = 10
$StartupTimeoutSeconds = 45
$GracefulStopWaitSeconds = 5
$TerminateWaitSeconds = 3
# ---------------------------------------------------------------------------

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Split-Path -Parent $ScriptDirectory
$WindowsPython = Join-Path $RepositoryRoot "venv\Scripts\python.exe"
$AriTarget = "${AriUser}@${AriHost}"
$DemoExitCode = 1
$AskPassFile = $null
$PreviousSshAskPass = $env:SSH_ASKPASS
$PreviousSshAskPassRequire = $env:SSH_ASKPASS_REQUIRE
$PreviousDisplay = $env:DISPLAY

function Write-Step {
    param([string] $Message)
    Write-Host "`n[ARI START] $Message" -ForegroundColor Cyan
}

function ConvertTo-RemoteBashCommand {
    param([string] $Script)

    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Script)
    $Encoded = [Convert]::ToBase64String($Bytes)
    return "printf %s $Encoded | base64 --decode | /bin/bash"
}

function Invoke-Ssh {
    param(
        [string[]] $SshArguments,
        [string] $Description
    )

    & ssh.exe @SshArguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed (ssh exit code $LASTEXITCODE)."
    }
}

function Wait-HttpHealth {
    param(
        [string] $Uri,
        [int] $TimeoutSeconds,
        [string] $Label
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $LastFailure = $null
    while ([DateTime]::UtcNow -lt $Deadline) {
        try {
            $Response = Invoke-RestMethod -Uri $Uri -TimeoutSec 2
            if ($Response.ok -eq $true) {
                return $Response
            }
            $LastFailure = "$Label returned ok=$($Response.ok)."
        }
        catch {
            $LastFailure = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Label did not become healthy at $Uri. Last error: $LastFailure"
}

function Test-TcpPort {
    param(
        [string] $HostName,
        [int] $Port,
        [int] $TimeoutMilliseconds = 750
    )

    $Client = [System.Net.Sockets.TcpClient]::new()
    try {
        $Connect = $Client.ConnectAsync($HostName, $Port)
        if (-not $Connect.Wait($TimeoutMilliseconds)) {
            return $false
        }
        return $Client.Connected
    }
    catch {
        return $false
    }
    finally {
        $Client.Dispose()
    }
}

function Stop-RemotePythonProcess {
    param(
        [string] $Pattern,
        [string] $Label
    )

    $StopCommand = @"
if pgrep -f '$Pattern' >/dev/null; then
    echo 'Stopping $Label with SIGINT...'
    pkill -INT -f '$Pattern' || true
    sleep $GracefulStopWaitSeconds
fi
if pgrep -f '$Pattern' >/dev/null; then
    echo '$Label did not exit after SIGINT; sending SIGTERM...'
    pkill -TERM -f '$Pattern' || true
    sleep $TerminateWaitSeconds
fi
if pgrep -f '$Pattern' >/dev/null; then
    echo '$Label did not exit after SIGTERM; sending SIGKILL...' >&2
    pkill -KILL -f '$Pattern' || true
    sleep 1
fi
if pgrep -f '$Pattern' >/dev/null; then
    echo 'Could not stop $Label.' >&2
    exit 1
fi
echo '$Label is stopped.'
"@

    try {
        Invoke-Ssh `
            -SshArguments ($CommonSshArguments + @(
                $AriTarget,
                (ConvertTo-RemoteBashCommand $StopCommand)
            )) `
            -Description "Stopping $Label"
    }
    catch {
        Write-Warning $_.Exception.Message
    }
}

if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    throw "Windows OpenSSH is unavailable. Install the OpenSSH Client feature first."
}
if (-not (Test-Path -LiteralPath $WindowsPython -PathType Leaf)) {
    throw "Project Python was not found at $WindowsPython. Create/install the venv first."
}
if (Test-TcpPort -HostName "127.0.0.1" -Port $UiPort) {
    throw "Local UI port $UiPort is already in use. Stop the old ARI demo and retry."
}

$CommonSshArguments = @(
    "-o", "ConnectTimeout=$SshConnectTimeoutSeconds",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "PubkeyAuthentication=no",
    "-o", "PreferredAuthentications=password,keyboard-interactive"
)

# Windows OpenSSH has no password flag. SSH_ASKPASS supplies this demo's known
# password and the temporary helper is removed during shutdown.
$AskPassFile = Join-Path ([System.IO.Path]::GetTempPath()) (
    "ari-demo-askpass-{0}-{1}.cmd" -f $PID, [Guid]::NewGuid().ToString("N")
)
Set-Content -LiteralPath $AskPassFile -Encoding Ascii -Value @(
    "@echo off",
    "echo $SshPassword"
)
$env:SSH_ASKPASS = $AskPassFile
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "ari-demo"

try {
    Write-Step "Ensuring both ARI services are running on $AriTarget"
    $StartCommand = @"
set -e
robond_is_running() {
    pgrep -f '^([^ ]*/)?python3 -m robond\.robot\.proxy\.ari_proxy($| )' >/dev/null
}
sidecar_is_running() {
    pgrep -f '^([^ ]*/)?python3 /home/pal/ari_social_proxy\.py($| )' >/dev/null
}
port_is_listening() {
    ss -ltnH 2>/dev/null | grep -q ":`$1 "
}

test -x '$RemoteVenv/bin/python3'
test -f '$RemoteSidecar' || {
    echo 'Missing $RemoteSidecar. Run .\ari\ari_save_social.ps1 first.' >&2
    exit 1
}

if robond_is_running && port_is_listening $RoboBondPort; then
    echo 'ARI RoboBOND proxy is already running.'
elif port_is_listening $RoboBondPort; then
    echo 'Port $RoboBondPort is occupied by an unknown process.' >&2
    exit 1
else
    nohup /bin/bash -ic 'source $RemoteVenv/bin/activate; exec python3 -m robond.robot.proxy.ari_proxy --port $RoboBondPort --language $AriLanguage' > '$RemoteRoboBondLog' 2>&1 < /dev/null &
    sleep 3
    if ! robond_is_running; then
        echo 'ARI RoboBOND proxy failed to start. Last log lines:' >&2
        tail -n 40 '$RemoteRoboBondLog' >&2 || true
        exit 1
    fi
    echo 'ARI RoboBOND proxy started; log: $RemoteRoboBondLog'
fi

if sidecar_is_running && port_is_listening $SocialControlPort; then
    echo 'ARI social sidecar is already running.'
elif port_is_listening $SocialControlPort; then
    echo 'Port $SocialControlPort is occupied by an unknown process.' >&2
    exit 1
else
    nohup /bin/bash -ic 'source $RemoteVenv/bin/activate; exec python3 $RemoteSidecar --port $SocialControlPort' > '$RemoteSidecarLog' 2>&1 < /dev/null &
    sleep 3
    if ! sidecar_is_running; then
        echo 'ARI social sidecar failed to start. Last log lines:' >&2
        tail -n 40 '$RemoteSidecarLog' >&2 || true
        exit 1
    fi
    echo 'ARI social sidecar started; log: $RemoteSidecarLog'
fi
"@
    Invoke-Ssh `
        -SshArguments ($CommonSshArguments + @(
            $AriTarget,
            (ConvertTo-RemoteBashCommand $StartCommand)
        )) `
        -Description "Starting ARI services"

    $RoboBondHealth = Wait-HttpHealth `
        -Uri "http://${AriHost}:${RoboBondPort}/health" `
        -TimeoutSeconds $StartupTimeoutSeconds `
        -Label "ARI RoboBOND proxy"
    Write-Host "[ARI READY] RoboBOND proxy is healthy." -ForegroundColor Green

    $SidecarHealth = Wait-HttpHealth `
        -Uri "http://${AriHost}:${SocialControlPort}/health" `
        -TimeoutSeconds $StartupTimeoutSeconds `
        -Label "ARI social sidecar"
    if ($SidecarHealth.sidecar_version -ne $ExpectedSidecarVersion) {
        throw (
            "ARI is running sidecar '$($SidecarHealth.sidecar_version)' instead of " +
            "'$ExpectedSidecarVersion'. Run .\ari\ari_save_social.ps1."
        )
    }
    if ($SidecarHealth.movement_available -ne $true) {
        throw "ARI sidecar is healthy, but safe manual movement is unavailable."
    }
    if ($SidecarHealth.neck_enabled -ne $true) {
        throw (
            "ARI sidecar could not enable neck tracking: " +
            "$($SidecarHealth.neck_error)"
        )
    }
    Write-Host (
        "[ARI READY] $($SidecarHealth.sidecar_version); movement available; " +
        "neck_enabled=$($SidecarHealth.neck_enabled)."
    ) -ForegroundColor Green
    if ($SidecarHealth.tablet_available -ne $true) {
        Write-Warning (
            "ARI tablet navigation is safely disabled because the internal " +
            "Main/Zoek_de_verschillen page URIs have not been verified and " +
            "configured. BABBEL remains available; see ari\README.md."
        )
    }
    Write-Host "[ARI READY] Operator UI: http://127.0.0.1:${UiPort}"

    Write-Step "Starting the Windows microphone demo"
    Push-Location $RepositoryRoot
    try {
        $PythonArguments = @(
            "-m", "ari.ari_mic_test",
            "--host", $AriHost,
            "--port", "$RoboBondPort",
            "--control-port", "$SocialControlPort",
            "--device", "$MicrophoneDevice",
            "--ui",
            "--ui-port", "$UiPort"
        ) + $DemoArguments
        & $WindowsPython @PythonArguments
        $DemoExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($DemoExitCode -ne 0) {
        throw "ari.ari_mic_test exited with code $DemoExitCode."
    }
}
finally {
    Write-Host "`n[ARI STOP] Beginning graceful shutdown..." -ForegroundColor Yellow

    Write-Host "[ARI STOP] 1/2 Stopping ROS social sidecar..."
    Stop-RemotePythonProcess `
        -Pattern '^([^ ]*/)?python3 /home/pal/ari_social_proxy\.py($| )' `
        -Label "ari_social_proxy.py"

    Write-Host "[ARI STOP] 2/2 Stopping RoboBOND proxy..."
    Stop-RemotePythonProcess `
        -Pattern '^([^ ]*/)?python3 -m robond\.robot\.proxy\.ari_proxy($| )' `
        -Label "ari_proxy"

    if ($null -ne $AskPassFile) {
        Remove-Item -LiteralPath $AskPassFile -Force -ErrorAction SilentlyContinue
    }
    if ($null -eq $PreviousSshAskPass) {
        Remove-Item Env:SSH_ASKPASS -ErrorAction SilentlyContinue
    }
    else {
        $env:SSH_ASKPASS = $PreviousSshAskPass
    }
    if ($null -eq $PreviousSshAskPassRequire) {
        Remove-Item Env:SSH_ASKPASS_REQUIRE -ErrorAction SilentlyContinue
    }
    else {
        $env:SSH_ASKPASS_REQUIRE = $PreviousSshAskPassRequire
    }
    if ($null -eq $PreviousDisplay) {
        Remove-Item Env:DISPLAY -ErrorAction SilentlyContinue
    }
    else {
        $env:DISPLAY = $PreviousDisplay
    }

    Write-Host "[ARI STOP] Shutdown sequence complete." -ForegroundColor Green
}
