# ARI demo: social attention and operator UI

The normal RoboBOND ARI proxy remains on port `7342`. The optional
`ari_social_proxy.py` process runs beside it on port `7343` and controls only:

- PAL attention policies (`IDLE_SOCIAL` in standby and `FOCUSED_SOCIAL` during
  a conversation);
- PAL gaze-manager eyes-and-neck coordination through its supported
  `/gaze_manager/enable_neck` service;
- the `neutral` expression plus a brief greeting smile when a stable face is
  newly acquired (and `happy` while ARI speaks during a conversation);
- guarded, press-and-hold manual base movement.

It does not publish head or gaze targets, replace RoboBOND, perform autonomous
navigation, or open/change anything on ARI's tablet.

Leave ARI's tablet on its existing **Main** page. This implementation makes no
tablet navigation request, so it neither guesses a PAL page identifier nor
creates a persistent display override.

## Save/deploy sidecar changes

The normal workflow is one PowerShell command from the
`social-robots-demo` directory. It uploads the local sidecar, validates it,
backs up the previous remote file, restarts only the sidecar, and checks its
version and smile configuration:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ari\ari_save_social.ps1
```

The script uses the configured `pal` SSH password through a temporary
`SSH_ASKPASS` helper and removes that helper afterwards. It does not create SSH
keys. The previous remote sidecar remains available as
`/home/pal/ari_social_proxy.py.bak`.

### Manual deployment fallback

Run these commands in PowerShell from the `social-robots-demo` directory:

```powershell
scp .\ari\ari_social_proxy.py pal@10.96.21.217:/home/pal/ari_social_proxy.py.new
ssh pal@10.96.21.217
```

Then, in the ARI SSH session:

```bash
source ~/robond-venv/bin/activate
python3 -m py_compile /home/pal/ari_social_proxy.py.new
test ! -f /home/pal/ari_social_proxy.py || cp -p /home/pal/ari_social_proxy.py /home/pal/ari_social_proxy.py.bak
mv /home/pal/ari_social_proxy.py.new /home/pal/ari_social_proxy.py
```

The existing file, when present, is retained as
`/home/pal/ari_social_proxy.py.bak`. To roll back:

```bash
cp -p /home/pal/ari_social_proxy.py.bak /home/pal/ari_social_proxy.py
```

## Start the complete demo automatically

After deploying the current sidecar once, start everything from Windows with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ari\start_ari_demo.ps1
```

This starts or reuses both remote ARI services, verifies RoboBOND, sidecar
version, neck control, and manual movement, and then starts the Windows
microphone process with device 3 and the local operator UI. Open
<http://127.0.0.1:8765>. Press `Ctrl+C` in this PowerShell window to stop the
microphone process, social sidecar, and RoboBOND proxy in that order.

The deploy step is deliberately separate: `start_ari_demo.ps1` does not upload
the sidecar on every run. If its version check fails, run
`ari_save_social.ps1` once and start again.

### Manual start fallback

Use two ARI SSH terminals. In the first, start the unchanged RoboBOND proxy:

```bash
ssh pal@10.96.21.217
source ~/robond-venv/bin/activate
python3 -m robond.robot.proxy.ari_proxy --port 7342 --language nl_NL
```

In the second, start the social sidecar:

```bash
ssh pal@10.96.21.217
source ~/robond-venv/bin/activate
python3 /home/pal/ari_social_proxy.py --port 7343
```

Check it from the laptop:

```powershell
Invoke-RestMethod http://10.96.21.217:7343/health
```

The result should report `ok: true`. `policy` should initially be
`IDLE_SOCIAL`, and `tablet_controlled` must remain `false`.

Finally, from this repository on the laptop:

```powershell
.\venv\Scripts\Activate.ps1
python -m ari.ari_mic_test --ui
```

If Windows chose the wrong microphone, list and select it explicitly:

```powershell
python -m ari.ari_mic_test --list-devices
python -m ari.ari_mic_test --ui --device 3
```

Open <http://127.0.0.1:8765> on the laptop. The page shows the ordered
visitor/ARI transcript, current phase, listening/speaking/thinking state,
attention mode, expression, connection state, movement controls, and a
**Terug naar standby** button.

The browser polls once per second, as in `hackathon2026-main`. Those routine
`GET /state` requests are intentionally not printed in the terminal.

Movement is intentionally press-and-hold. Releasing a pointer/key, changing
browser focus, losing the sidecar, entering emergency state, or missing the
command heartbeat stops the base. Forward motion is blocked by nearby laser or
torso-depth obstacles when either sensor is live. As in the hackathon
controller, manual movement can still be used cautiously without an obstacle
sensor; the UI reports that degraded state explicitly. Movement stays disabled
when the base command subscriber or robot connection is unavailable.

The conversation still works when port `7343` is unavailable. In that degraded
mode, attention and movement are disabled and expressions use the existing
RoboBOND proxy when possible.

If the laptop prints an `ARI SOCIAL` error containing `WinError 10061`, the
microphone is not the failing component: nothing is listening on ARI port
`7343`. Keep the sidecar command above running in its own SSH terminal and
check `/health` again. The local page on port `8765` belongs to the laptop
microphone process, so that page also closes when `ari_mic_test` exits.

The one-second early microphone overlap remains enabled for questions. The
adapter does not overlap a goodbye and filters close multi-word matches to
ARI's own recent speech before they reach the conversation engine.

## Stop and restore standby

First click **Terug naar standby**, then stop the laptop process with `Ctrl+C`.
Stop the social sidecar with `Ctrl+C` in its SSH terminal. Its shutdown handler
publishes zero velocity, returns the face to neutral, and requests
`IDLE_SOCIAL`. Stop the separate RoboBOND proxy only when it is no longer needed.

## Checks before physical testing

The sidecar intentionally does not stop any existing ROS node. Before testing,
inspect the possible publishers and the attention service on ARI:

```bash
rosservice info /attention_manager/set_policy
rosservice info /gaze_manager/enable_neck
rostopic info /robot_face/expression
rostopic info /mobile_base_controller/cmd_vel
rostopic hz /torso_front_camera/depth/image_rect_raw
rostopic info /humans/faces/tracked
rostopic info /humans/persons/tracked
```

If an older demo (for example `/welcome_demo`) is also publishing expressions,
attention, or velocity, stop that demo through its normal launcher before
starting this one. Do not kill unfamiliar ROS nodes automatically.
