# Save qt/stand.py to QTPC as stand_proxy.py and restart only the tracker.
# The previous remote file is retained as stand_proxy.py.bak for rollback.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Adjustable deployment settings.
# ---------------------------------------------------------------------------
$GatewayHost = "10.96.21.245"
$GatewayUser = "developer"
$QtPcHost = "192.168.100.2"
$QtPcUser = "qtrobot"
$SshPassword = "qtrobot"

$RemoteDirectory = "/home/qtrobot/catkin_ws/src/qt_proxy"
$RemoteStandFile = "$RemoteDirectory/stand_proxy.py"
$RemoteNewFile = "$RemoteDirectory/stand_proxy.new.py"
$RemoteBackupFile = "$RemoteDirectory/stand_proxy.py.bak"
$RemoteLogFile = "/tmp/qt-stand.log"
$ExpectedStandVersion = "demo-face-grace-12s-greeting-v2"

$SshConnectTimeoutSeconds = 10
$GracefulStopWaitSeconds = 5
$TerminateWaitSeconds = 3
$TrackerStartupWaitSeconds = 4
$StandControlPort = 7343
# ---------------------------------------------------------------------------

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalStandFile = Join-Path $ScriptDirectory "stand.py"
$GatewayTarget = "${GatewayUser}@${GatewayHost}"
$QtPcTarget = "${QtPcUser}@${QtPcHost}"
$AskPassFile = $null
$PreviousSshAskPass = $env:SSH_ASKPASS
$PreviousSshAskPassRequire = $env:SSH_ASKPASS_REQUIRE
$PreviousDisplay = $env:DISPLAY

function ConvertTo-RemoteBashCommand {
    param([string] $Script)

    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Script)
    $Encoded = [Convert]::ToBase64String($Bytes)
    return "printf %s $Encoded | base64 --decode | /bin/bash"
}

if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    throw "Windows OpenSSH is unavailable. Install the OpenSSH Client feature first."
}
if (-not (Get-Command scp.exe -ErrorAction SilentlyContinue)) {
    throw "Windows SCP is unavailable. Install the OpenSSH Client feature first."
}
if (-not (Test-Path -LiteralPath $LocalStandFile -PathType Leaf)) {
    throw "Local tracker was not found at $LocalStandFile."
}
if (-not (Select-String `
    -LiteralPath $LocalStandFile `
    -SimpleMatch "STAND_BUILD_VERSION = `"$ExpectedStandVersion`"" `
    -Quiet
)) {
    throw "Local stand.py does not contain expected version $ExpectedStandVersion."
}

$CommonSshArguments = @(
    "-o", "ConnectTimeout=$SshConnectTimeoutSeconds",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "PubkeyAuthentication=no",
    "-o", "PreferredAuthentications=password,keyboard-interactive"
)

$AskPassFile = Join-Path ([System.IO.Path]::GetTempPath()) (
    "qt-deploy-askpass-{0}-{1}.cmd" -f $PID, [Guid]::NewGuid().ToString("N")
)
Set-Content -LiteralPath $AskPassFile -Encoding Ascii -Value @(
    "@echo off",
    "echo $SshPassword"
)
$env:SSH_ASKPASS = $AskPassFile
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "qt-deploy"

try {
    Write-Host "[QT DEPLOY] Uploading $LocalStandFile" -ForegroundColor Cyan
    $ScpArguments = $CommonSshArguments + @(
        "-J", $GatewayTarget,
        $LocalStandFile,
        "${QtPcTarget}:$RemoteNewFile"
    )
    & scp.exe @ScpArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Uploading stand.py failed (scp exit code $LASTEXITCODE)."
    }

    Write-Host "[QT DEPLOY] Validating, installing, and restarting the tracker..." `
        -ForegroundColor Cyan
    $DeployCommand = @"
set -e
tracker_is_running() {
    pgrep -f '^([^ ]*/)?python3 .*/stand_proxy\.py($| )' >/dev/null
}
tracker_is_listening() {
    ss -ltnH 2>/dev/null | grep -q ':$StandControlPort '
}
start_tracker() {
    cd '$RemoteDirectory'
    nohup /bin/bash -ic 'source /opt/ros/noetic/setup.bash; test ! -f /home/qtrobot/catkin_ws/devel/setup.bash || source /home/qtrobot/catkin_ws/devel/setup.bash; exec /usr/bin/python3 $RemoteStandFile' > '$RemoteLogFile' 2>&1 < /dev/null &
}

echo '[remote 1/5] Checking uploaded file...'
test -f '$RemoteNewFile'
echo '[remote 2/5] Checking Python syntax...'
/usr/bin/python3 -m py_compile '$RemoteNewFile'
echo '[remote 3/5] Backing up and stopping previous tracker...'

if test -f '$RemoteStandFile'; then
    cp -p '$RemoteStandFile' '$RemoteBackupFile'
fi

if tracker_is_running; then
    echo 'Stopping the current tracker with SIGINT...'
    pkill -INT -f '^([^ ]*/)?python3 .*/stand_proxy\.py($| )' || true
    sleep $GracefulStopWaitSeconds
fi
if tracker_is_running; then
    echo 'Tracker did not exit after SIGINT; sending SIGTERM...'
    pkill -TERM -f '^([^ ]*/)?python3 .*/stand_proxy\.py($| )' || true
    sleep $TerminateWaitSeconds
fi
if tracker_is_running; then
    echo 'Tracker did not exit after SIGTERM; sending SIGKILL...' >&2
    pkill -KILL -f '^([^ ]*/)?python3 .*/stand_proxy\.py($| )' || true
    sleep 1
fi
if tracker_is_running; then
    echo 'Could not stop the previous tracker.' >&2
    exit 1
fi

mv '$RemoteNewFile' '$RemoteStandFile'
echo '[remote 4/5] Starting deployed tracker...'
start_tracker
sleep $TrackerStartupWaitSeconds

echo '[remote 5/5] Checking process, port, version, and grace configuration...'
if ! tracker_is_running || ! tracker_is_listening || ! /usr/bin/python3 -c 'import json, urllib.request; data=json.load(urllib.request.urlopen("http://127.0.0.1:$StandControlPort/health", timeout=3)); assert data.get("stand_version") == "$ExpectedStandVersion"; print(json.dumps(data, sort_keys=True))'; then
    echo 'The new tracker failed its health check. Last log lines:' >&2
    tail -n 40 '$RemoteLogFile' >&2 || true
    pkill -TERM -f '^([^ ]*/)?python3 .*/stand_proxy\.py($| )' || true
    sleep 2
    if test -f '$RemoteBackupFile'; then
        echo 'Restoring stand_proxy.py.bak...' >&2
        cp -p '$RemoteBackupFile' '$RemoteStandFile'
        start_tracker
    fi
    exit 1
fi

echo 'Deployment complete: $RemoteStandFile'
echo 'Backup retained at: $RemoteBackupFile'
"@

    $RemoteDeployCommand = ConvertTo-RemoteBashCommand $DeployCommand
    & ssh.exe @CommonSshArguments `
        -J $GatewayTarget `
        $QtPcTarget `
        $RemoteDeployCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Remote tracker deployment failed (ssh exit code $LASTEXITCODE)."
    }

    Write-Host (
        "[QT DEPLOY] Successfully updated stand.py on the proxy!"
    ) -ForegroundColor Green
}
finally {
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
}
