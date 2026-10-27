# Save ari/ari_social_proxy.py to ARI and restart only the ROS social sidecar.
# The previous remote file is retained as ari_social_proxy.py.bak.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Adjustable deployment settings.
# ---------------------------------------------------------------------------
$AriHost = "10.96.21.217"
$AriUser = "pal"
$SshPassword = "pal"

$RemoteVenv = "/home/pal/robond-venv"
$RemoteSidecar = "/home/pal/ari_social_proxy.py"
$RemoteNewFile = "/home/pal/ari_social_proxy.new.py"
$RemoteBackupFile = "/home/pal/ari_social_proxy.py.bak"
$RemoteLogFile = "/tmp/ari-social-proxy.log"
$SocialControlPort = 7343
$ExpectedSidecarVersion = "ari-social-v0.4.2-guardian-operator-emotes"
$ExpectedSmileSeconds = 2.0

$SshConnectTimeoutSeconds = 10
$GracefulStopWaitSeconds = 5
$TerminateWaitSeconds = 3
$SidecarStartupWaitSeconds = 10
# ---------------------------------------------------------------------------

$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalSidecar = Join-Path $ScriptDirectory "ari_social_proxy.py"
$AriTarget = "${AriUser}@${AriHost}"
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
if (-not (Test-Path -LiteralPath $LocalSidecar -PathType Leaf)) {
    throw "Local ARI sidecar was not found at $LocalSidecar."
}
if (-not (Select-String `
    -LiteralPath $LocalSidecar `
    -SimpleMatch "SIDECAR_VERSION = `"$ExpectedSidecarVersion`"" `
    -Quiet
)) {
    throw "Local sidecar does not contain expected version $ExpectedSidecarVersion."
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
    "ari-deploy-askpass-{0}-{1}.cmd" -f $PID, [Guid]::NewGuid().ToString("N")
)
Set-Content -LiteralPath $AskPassFile -Encoding Ascii -Value @(
    "@echo off",
    "echo $SshPassword"
)
$env:SSH_ASKPASS = $AskPassFile
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "ari-deploy"

try {
    Write-Host "[ARI DEPLOY] Uploading $LocalSidecar" -ForegroundColor Cyan
    & scp.exe @CommonSshArguments $LocalSidecar "${AriTarget}:$RemoteNewFile"
    if ($LASTEXITCODE -ne 0) {
        throw "Uploading ari_social_proxy.py failed (scp exit code $LASTEXITCODE)."
    }

    Write-Host "[ARI DEPLOY] Validating, installing, and restarting the sidecar..." `
        -ForegroundColor Cyan
    $DeployCommand = @"
set -e
sidecar_is_running() {
    pgrep -f '^([^ ]*/)?python3 /home/pal/ari_social_proxy\.py($| )' >/dev/null
}
sidecar_is_listening() {
    ss -ltnH 2>/dev/null | grep -q ':$SocialControlPort '
}
start_sidecar() {
    nohup /bin/bash -ic 'source $RemoteVenv/bin/activate; exec python3 $RemoteSidecar --port $SocialControlPort' > '$RemoteLogFile' 2>&1 < /dev/null &
}

echo '[remote 1/5] Checking uploaded file...'
test -f '$RemoteNewFile'
test -x '$RemoteVenv/bin/python3'

echo '[remote 2/5] Checking Python syntax...'
'$RemoteVenv/bin/python3' -m py_compile '$RemoteNewFile'

echo '[remote 3/5] Backing up and stopping the previous sidecar...'
if test -f '$RemoteSidecar'; then
    cp -p '$RemoteSidecar' '$RemoteBackupFile'
fi
if sidecar_is_running; then
    echo 'Stopping the current sidecar with SIGINT...'
    pkill -INT -f '^([^ ]*/)?python3 /home/pal/ari_social_proxy\.py($| )' || true
    sleep $GracefulStopWaitSeconds
fi
if sidecar_is_running; then
    echo 'Sidecar did not exit after SIGINT; sending SIGTERM...'
    pkill -TERM -f '^([^ ]*/)?python3 /home/pal/ari_social_proxy\.py($| )' || true
    sleep $TerminateWaitSeconds
fi
if sidecar_is_running; then
    echo 'Sidecar did not exit after SIGTERM; sending SIGKILL...' >&2
    pkill -KILL -f '^([^ ]*/)?python3 /home/pal/ari_social_proxy\.py($| )' || true
    sleep 1
fi
if sidecar_is_running; then
    echo 'Could not stop the previous sidecar.' >&2
    exit 1
fi

mv '$RemoteNewFile' '$RemoteSidecar'
echo '[remote 4/5] Starting deployed sidecar...'
start_sidecar
sleep $SidecarStartupWaitSeconds

echo '[remote 5/5] Checking process, port, version, and smile configuration...'
if ! sidecar_is_running || ! sidecar_is_listening || ! /usr/bin/python3 -c 'import json, urllib.request; data=json.load(urllib.request.urlopen("http://127.0.0.1:$SocialControlPort/health", timeout=3)); assert data.get("sidecar_version") == "$ExpectedSidecarVersion"; assert data.get("face_greeting_smile_seconds") == $ExpectedSmileSeconds; print(json.dumps(data, sort_keys=True))'; then
    echo 'The new sidecar failed its health check. Last log lines:' >&2
    tail -n 50 '$RemoteLogFile' >&2 || true
    pkill -TERM -f '^([^ ]*/)?python3 /home/pal/ari_social_proxy\.py($| )' || true
    sleep 2
    if test -f '$RemoteBackupFile'; then
        echo 'Restoring ari_social_proxy.py.bak...' >&2
        cp -p '$RemoteBackupFile' '$RemoteSidecar'
        start_sidecar
    fi
    exit 1
fi

echo 'Deployment complete: $RemoteSidecar'
echo 'Backup retained at: $RemoteBackupFile'
"@

    & ssh.exe @CommonSshArguments `
        $AriTarget `
        (ConvertTo-RemoteBashCommand $DeployCommand)
    if ($LASTEXITCODE -ne 0) {
        throw "Remote sidecar deployment failed (ssh exit code $LASTEXITCODE)."
    }

    Write-Host (
        "[ARI DEPLOY] Success. ARI is running $ExpectedSidecarVersion " +
        "with a ${ExpectedSmileSeconds}-second face greeting smile."
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
