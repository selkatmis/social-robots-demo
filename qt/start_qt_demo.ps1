# Start the complete QT demo from Windows.
#
# This script starts (or reuses) the relay on QTRD, the RoboBOND proxy and
# standby tracker on QTPC, opens the local port-7343 tunnel, and runs the
# microphone conversation. The tutor phase controls DEMO/STANDBY automatically.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $DemoArguments
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Adjustable connection and remote-path settings.
# ---------------------------------------------------------------------------
$GatewayHost = "10.96.21.245"
$GatewayUser = "developer"
$QtPcHost = "192.168.100.2"
$QtPcUser = "qtrobot"
# Both SSH accounts currently use this password. It is intentionally stored in
# plain text here so this demo launcher can run without SSH keys or prompts.
$SshPassword = "qtrobot"

$RoboBondPort = 7342
$StandControlPort = 7343
$LocalStandControlPort = 7343

$RelayWorkingDirectory = "/home/developer/qtrp_relay/relay_env"
$QtProxyWorkingDirectory = "/home/qtrobot/catkin_ws/src/qt_proxy"
$QtProxyPython = "/home/qtrobot/catkin_ws/src/qt_proxy/venv/bin/python3"
$QtStandPython = "/usr/bin/python3"
$QtStandFile = "stand_proxy.py"

$QtLanguage = "nl-NL"
$QtVolume = 130
$ExpectedStandVersion = "demo-face-grace-12s-v1"
$SshConnectTimeoutSeconds = 10
$StartupTimeoutSeconds = 30
$GracefulStopWaitSeconds = 5
$TerminateWaitSeconds = 3

# ---------------------------------------------------------------------------

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepositoryRoot = Split-Path -Parent $ScriptDirectory
$WindowsPython = Join-Path $RepositoryRoot "venv\Scripts\python.exe"
$GatewayTarget = "${GatewayUser}@${GatewayHost}"
$QtPcTarget = "${QtPcUser}@${QtPcHost}"
$TunnelProcess = $null
$DemoExitCode = 1
$AskPassFile = $null
$PreviousSshAskPass = $env:SSH_ASKPASS
$PreviousSshAskPassRequire = $env:SSH_ASKPASS_REQUIRE
$PreviousDisplay = $env:DISPLAY

function Write-Step {
    param([string] $Message)
    Write-Host "`n[QT START] $Message" -ForegroundColor Cyan
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

function Wait-TcpPort {
    param(
        [string] $HostName,
        [int] $Port,
        [int] $TimeoutSeconds,
        [System.Diagnostics.Process] $OwnerProcess = $null
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($null -ne $OwnerProcess) {
            $OwnerProcess.Refresh()
            if ($OwnerProcess.HasExited) {
                throw "The SSH tunnel stopped before ${HostName}:$Port became ready."
            }
        }
        if (Test-TcpPort -HostName $HostName -Port $Port) {
            return
        }
        Start-Sleep -Milliseconds 400
    }
    throw "Timed out waiting for ${HostName}:$Port after $TimeoutSeconds seconds."
}

function Wait-StandHealth {
    param([int] $TimeoutSeconds)

    $HealthUri = "http://127.0.0.1:${LocalStandControlPort}/health"
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $LastFailure = $null
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($null -ne $TunnelProcess) {
            $TunnelProcess.Refresh()
            if ($TunnelProcess.HasExited) {
                throw "The SSH tunnel stopped before stand.py became healthy."
            }
        }
        try {
            $Response = Invoke-RestMethod -Uri $HealthUri -TimeoutSec 2
            if ($Response.ok -eq $true) {
                return $Response
            }
        }
        catch {
            $LastFailure = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    throw "stand.py did not become healthy at $HealthUri. Last error: $LastFailure"
}

function Stop-RemotePythonProcess {
    param(
        [string] $Target,
        [string] $Pattern,
        [string] $Label,
        [switch] $ViaGateway
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

    $Arguments = $CommonSshArguments
    if ($ViaGateway) {
        $Arguments += @("-J", $GatewayTarget)
    }
    $RemoteStopCommand = ConvertTo-RemoteBashCommand $StopCommand
    $Arguments += @($Target, $RemoteStopCommand)

    try {
        Invoke-Ssh -SshArguments $Arguments -Description "Stopping $Label"
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
if (Test-TcpPort -HostName "127.0.0.1" -Port $LocalStandControlPort) {
    throw "Local port $LocalStandControlPort is already in use. Close the old SSH tunnel and retry."
}

$CommonSshArguments = @(
    "-o", "ConnectTimeout=$SshConnectTimeoutSeconds",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "PubkeyAuthentication=no",
    "-o", "PreferredAuthentications=password,keyboard-interactive"
)

# Windows OpenSSH has no password command-line flag. SSH_ASKPASS supplies the
# configured password without an interactive prompt. The helper is unique to
# this process and is removed when all remote processes have been stopped.
$AskPassFile = Join-Path ([System.IO.Path]::GetTempPath()) (
    "qt-demo-askpass-{0}-{1}.cmd" -f $PID, [Guid]::NewGuid().ToString("N")
)
Set-Content -LiteralPath $AskPassFile -Encoding Ascii -Value @(
    "@echo off",
    "echo $SshPassword"
)
$env:SSH_ASKPASS = $AskPassFile
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "qt-demo"

try {
    Write-Step "Ensuring the QTRP relay is running on $GatewayTarget"
    $RelayCommand = @"
set -e
relay_is_running() {
    pgrep -f '^([^ ]*/)?python3 -m robond\.robot\.proxy\.relay($| )' >/dev/null
}
relay_is_listening() {
    ss -ltnH 2>/dev/null | grep -q ':$RoboBondPort '
}
if relay_is_running && relay_is_listening; then
    echo 'qtrp_relay is already running.'
else
    cd '$RelayWorkingDirectory'
    nohup python3 -m robond.robot.proxy.relay > /tmp/qtrp-relay.log 2>&1 < /dev/null &
    sleep 2
    if ! relay_is_running || ! relay_is_listening; then
        echo 'qtrp_relay failed to start. Last log lines:' >&2
        tail -n 30 /tmp/qtrp-relay.log >&2 || true
        exit 1
    fi
    echo 'qtrp_relay started; log: /tmp/qtrp-relay.log'
fi
"@
    Invoke-Ssh -Description "Starting qtrp_relay" -SshArguments (
        $CommonSshArguments + @(
            $GatewayTarget,
            (ConvertTo-RemoteBashCommand $RelayCommand)
        )
    )
    Wait-TcpPort -HostName $GatewayHost -Port $RoboBondPort `
        -TimeoutSeconds $StartupTimeoutSeconds

    Write-Step "Ensuring the RoboBOND proxy and face tracker are running on QTPC"
    $QtPcCommand = @"
set -e
cd '$QtProxyWorkingDirectory'

proxy_is_running() {
    pgrep -f '^([^ ]*/)?python3 -m robond\.robot\.proxy\.qt_proxy($| )' >/dev/null
}
stand_is_running() {
    pgrep -f '^([^ ]*/)?python3 .*/stand_proxy\.py($| )' >/dev/null
}

if proxy_is_running; then
    echo 'qt_proxy is already running.'
else
    test -x '$QtProxyPython'
    nohup /bin/bash -ic 'source /opt/ros/noetic/setup.bash; test ! -f /home/qtrobot/catkin_ws/devel/setup.bash || source /home/qtrobot/catkin_ws/devel/setup.bash; exec $QtProxyPython -m robond.robot.proxy.qt_proxy --port $RoboBondPort --language $QtLanguage --volume $QtVolume' > /tmp/qt-robond-proxy.log 2>&1 < /dev/null &
    sleep 2
    if ! proxy_is_running; then
        echo 'qt_proxy failed to start. Last log lines:' >&2
        tail -n 30 /tmp/qt-robond-proxy.log >&2 || true
        exit 1
    fi
    echo 'qt_proxy started; log: /tmp/qt-robond-proxy.log'
fi

if stand_is_running; then
    echo 'stand_proxy.py is already running.'
else
    test -f '$QtStandFile'
    nohup /bin/bash -ic 'source /opt/ros/noetic/setup.bash; test ! -f /home/qtrobot/catkin_ws/devel/setup.bash || source /home/qtrobot/catkin_ws/devel/setup.bash; exec $QtStandPython $QtProxyWorkingDirectory/$QtStandFile' > /tmp/qt-stand.log 2>&1 < /dev/null &
    sleep 2
    if ! stand_is_running; then
        echo 'stand_proxy.py failed to start. Last log lines:' >&2
        tail -n 30 /tmp/qt-stand.log >&2 || true
        exit 1
    fi
    echo 'stand_proxy.py started; log: /tmp/qt-stand.log'
fi
"@
    Invoke-Ssh -Description "Starting QTPC processes" -SshArguments (
        $CommonSshArguments + @(
            "-J",
            $GatewayTarget,
            $QtPcTarget,
            (ConvertTo-RemoteBashCommand $QtPcCommand)
        )
    )

    Write-Step "Opening localhost:$LocalStandControlPort to QTPC:$StandControlPort"
    $TunnelArguments = $CommonSshArguments + @(
        "-o", "ExitOnForwardFailure=yes",
        "-J", $GatewayTarget,
        "-N",
        "-L", "${LocalStandControlPort}:127.0.0.1:${StandControlPort}",
        $QtPcTarget
    )
    $TunnelProcess = Start-Process -FilePath "ssh.exe" `
        -ArgumentList $TunnelArguments `
        -WindowStyle Hidden `
        -PassThru

    Wait-TcpPort -HostName "127.0.0.1" -Port $LocalStandControlPort `
        -TimeoutSeconds $StartupTimeoutSeconds -OwnerProcess $TunnelProcess
    $StandHealth = Wait-StandHealth -TimeoutSeconds $StartupTimeoutSeconds
    if ($StandHealth.stand_version -ne $ExpectedStandVersion) {
        throw (
            "QTPC is running stand.py version '$($StandHealth.stand_version)' " +
            "instead of '$ExpectedStandVersion'. Run .\qt\qt_save_stand.ps1."
        )
    }
    Write-Host "[QT READY] Stand tracker healthy (demo_active=$($StandHealth.demo_active))." `
        -ForegroundColor Green
    Write-Host (
        "[QT READY] Tracker $($StandHealth.stand_version): face-loss grace " +
        "$($StandHealth.standby_face_lost_grace_seconds)s standby / " +
        "$($StandHealth.demo_face_lost_grace_seconds)s demo."
    )

    try {
        $StandbyBody = '{"active":false}'
        $StandbyResponse = Invoke-RestMethod `
            -Method Put `
            -Uri "http://127.0.0.1:${LocalStandControlPort}/demo" `
            -ContentType "application/json" `
            -Body $StandbyBody `
            -TimeoutSec 3
        if ($StandbyResponse.ok -ne $true) {
            throw "stand.py rejected standby mode."
        }
    }
    catch {
        throw "Could not place stand.py in standby mode: $($_.Exception.Message)"
    }

    Write-Host "[QT READY] STANDBY is running; the tutor will enable DEMO after visitor engagement." `
        -ForegroundColor Green

    Write-Step "Starting the Windows microphone demo"
    Push-Location $RepositoryRoot
    try {
        $PythonArguments = @(
            "-m", "qt.qt_demo_test",
            "--host", $GatewayHost,
            "--port", "$RoboBondPort",
            "--stand-host", "127.0.0.1",
            "--stand-port", "$LocalStandControlPort"
        ) + $DemoArguments
        & $WindowsPython @PythonArguments
        $DemoExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($DemoExitCode -ne 0) {
        throw "qt.qt_demo_test exited with code $DemoExitCode."
    }
}
finally {
    Write-Host "`n[QT STOP] Beginning graceful shutdown..." -ForegroundColor Yellow

    if ($null -ne $TunnelProcess) {
        $TunnelProcess.Refresh()
        if (-not $TunnelProcess.HasExited) {
            try {
                Invoke-RestMethod `
                    -Method Put `
                    -Uri "http://127.0.0.1:${LocalStandControlPort}/demo" `
                    -ContentType "application/json" `
                    -Body '{"active":false}' `
                    -TimeoutSec 3 | Out-Null
                Write-Host "[QT STOP] Demo state restored to STANDBY."
            }
            catch {
                Write-Warning "Could not restore standby state before shutdown: $($_.Exception.Message)"
            }
        }
    }

    Write-Host "[QT STOP] 1/4 Stopping face tracker..."
    Stop-RemotePythonProcess `
        -Target $QtPcTarget `
        -Pattern '^([^ ]*/)?python3 .*/stand_proxy\.py($| )' `
        -Label "stand_proxy.py" `
        -ViaGateway

    Write-Host "[QT STOP] 2/4 Stopping RoboBOND proxy..."
    Stop-RemotePythonProcess `
        -Target $QtPcTarget `
        -Pattern '^([^ ]*/)?python3 -m robond\.robot\.proxy\.qt_proxy($| )' `
        -Label "qt_proxy" `
        -ViaGateway

    Write-Host "[QT STOP] 3/4 Stopping QTRP relay..."
    Stop-RemotePythonProcess `
        -Target $GatewayTarget `
        -Pattern '^([^ ]*/)?python3 -m robond\.robot\.proxy\.relay($| )' `
        -Label "qtrp_relay"

    Write-Host "[QT STOP] 4/4 Closing the port-7343 tunnel..."
    if ($null -ne $TunnelProcess) {
        $TunnelProcess.Refresh()
        if (-not $TunnelProcess.HasExited) {
            Stop-Process -Id $TunnelProcess.Id -Force
            $TunnelProcess.WaitForExit(3000) | Out-Null
        }
        $TunnelProcess.Dispose()
        $TunnelProcess = $null
    }

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

    Write-Host "[QT STOP] Shutdown sequence complete." -ForegroundColor Green
}
