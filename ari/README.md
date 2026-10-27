# ARI v0.4 demo: conversation, activity game and social attention

## Two conversations

ARI runs one of two conversations, both in Dutch:

- **guardian** (default): ARI is the guide in the central hall of the VUmc
  outpatient building. It helps older visitors, some with dementia, find
  their way and answers practical questions: short sentences, one step at a
  time, patient with repeated questions, and the date and time when someone
  is disoriented. When a visitor is confused, upset, looking for someone,
  wants to be accompanied or has an urgent problem, ARI says it is getting a
  staff member and the operator page shows an alert (**Ik kom eraan** lets ARI
  tell the visitor someone is coming). After 60 seconds without speech ARI
  forgets the visitor and goes back to standby.
- **companion**: the open conversation (`responses.py`, `prompts.py`).

```powershell
python -m ari.ari_mic_test --ui                    # guardian
python -m ari.ari_mic_test --ui --mode companion   # conversation and activities
python -m ari.ari_llm_test                         # type instead of talking
```

The guide's wording and rules are in `guardian_prompts.py`, the conversation
logic in `guardian.py`. Everything ARI knows about the hospital is in
`vumc_info.txt` (checked against amsterdamumc.nl on 26 October 2026); ARI
does not answer from anything else, so correct or extend that file.

## Operator actions

Besides the transcript, standby and the arrow pad, the operator server on
port `8765` can greet a visitor, let ARI say a typed line, change the face,
play a gesture (wave, nod, cheer), shake hands, play an emote and stop
everything. The robot hub (`python -m hub`, see the main README) puts all of
this on an iPad.

Emotes are the hackathon's FOAF emotes, in `emotes.json`: steps such as
`head 40 10 0.4`, `turn 30`, `gesture wave`, `say ...` and `music on`. The
file explains each step and is read fresh every time, so a new emote shows up
without a restart. The **Dans** has no driving on purpose, because people
stand close to ARI at the stand. For the handshake ARI offers its right hand
(`shake_right`) and watches the laser or torso camera for a hand within 70 cm.

The sidecar version `ari-social-v0.4.2-guardian-operator-emotes` adds what these need:
a face that holds for a few seconds, head moves (attention pauses and comes
back three seconds after the last one), turns and short drives measured with
odometry, dance music on ARI's speaker (`~/dance.wav`, `~/StillDRE.wav`, or a
tune it writes itself) and the speaker volume. Turns and drives go through the
same guarded velocity loop as the arrow pad, and the arrow pad or the stop
button always interrupts them. Deploy it once with `ari_save_social.ps1`.

## Social sidecar

The normal RoboBOND ARI proxy remains on port `7342`. The optional
`ari_social_proxy.py` process runs beside it on port `7343` and controls:

- PAL attention policies (`IDLE_SOCIAL` in standby and `FOCUSED_SOCIAL` during
  a conversation);
- PAL gaze-manager eyes-and-neck coordination through its supported
  `/gaze_manager/enable_neck` service;
- the `neutral` expression plus one two-second greeting smile for each newly
  stable anonymous face-tracker ID;
- `happy` only while ARI speaks during an active conversation or game;
- fail-closed navigation between two existing PAL Touch Web pages;
- guarded, press-and-hold manual base movement;
- explicit operator head moves, short measured turns/drives, music and volume.

Normal head/eye attention belongs to PAL. Explicit operator-emote head steps
temporarily pause it, then restore the active policy. The sidecar does not
replace RoboBOND, perform autonomous navigation, identify visitors from faces,
or create/replace tablet content.

## Visitor flow

The microphone and keyboard adapters enable the v0.4 flow:

```text
STANDBY -> INTRODUCTION -> ASK_NAME -> DAILY_CHECKIN -> ACTIVITY_CHOICE
        -> GEZELLIG_BABBELEN or ZOEK_DE_VERSCHILLEN
        -> ACTIVITY_CHOICE / GOODBYE -> STANDBY
```

The introduction explains ARI's social role once per visitor. A name remains
optional. ARI waves with the first greeting sentence through RoboBOND's native
`coordinated_action(..., gesture=Gesture.WAVE)` interface, as used in the
hackathon introduction. Later name questions and activity switches do not
trigger another wave. The check-in asks first how the visitor is and then, only when that
information was not already supplied, about plans for today. A direct request
to chat, play or stop skips unnecessary check-in questions.

`GEZELLIG_BABBELEN` reuses the existing structured conversation engine,
session memory, safety handling and three-to-four-question topic cycles. The
proactive pool now emphasizes daily life, meals, music, interests, memories,
open social-life questions and an occasional optional playful question.
Gardening is no longer proactively selected, but ARI can still follow it when
the visitor brings it up.

## Zoek de Verschillen data

`ari/difference_game_data.py` now contains the 15 differences supplied in
`ari/klokwijzer.txt`: clock hands, mountain, tongue, curtain, collar, cake
icing, bananas, biscuit, cupboard knob, objects on the cupboard, socks, flower,
the cat's object, the plate's face and the older woman's hair clip. Each entry
has an ID, directional descriptions, synonyms, accepted example observations
and a gentle hint. Edit this data module to refine the references later;
`klokwijzer.txt` is retained as the original source, not parsed at runtime.

These references describe **left and right**. The legacy internal field
`picture_a` holds the left reference and `picture_b` holds the right one. The
interpreter is explicitly told this. No A/B label mapping is assumed. If a
visitor uses A/B before the real tablet labels are verified, ARI asks them to
describe the difference using left/right. After checking the tablet, set
`PICTURE_A_SIDE` to `"links"` or `"rechts"` in the data module if needed.

The LLM interprets everyday Dutch, synonyms and recognizable ASR mistakes;
visitors do not have to reproduce the reference wording or describe both
halves if the difference is already specific. Just naming an object is not
enough. Python checks the proposed reference ID, feature, directional pair and
confidence. Explicit reversed directions are not corrected into good answers.
Python also deduplicates matches and
owns the score. Multiple verified differences in one utterance count once
each. A deliberately empty dataset still disables scoring safely.

The initial milestone is three unique matches. Continuing raises the total
target to eight (five more), and continuing again raises it to fifteen (seven
more). The next target is capped by the available dataset, and progress is
preserved across rounds and activity switches. If several differences in one
answer cross a boundary, the next offer accounts for the already found extras.
A visitor can stop immediately at any time. After the last difference ARI
offers a chat rather than asking for more differences.

Correct answers receive varied, restrained praise. Two unsuccessful attempts
give a comforting response and a hint about an unfound difference. Saying
that one does not know or asking for help gives a hint immediately. Hints
rotate, never award points and do not reveal the full answer. Correct answers
reset the attempt counter; repeats and technical failures do not count as
mistakes. All spoken wording uses respectful `u`/`uw` forms, and generated
informal address is rejected by the spoken-response validator.

Adjust `DEFAULT_TARGET_COUNT`, `ADDITIONAL_ROUND_COUNTS` (currently `5, 7`),
`MISTAKES_BEFORE_HINT` and `DEFAULT_MINIMUM_CONFIDENCE` at the beginning of
`ari/difference_game.py`.

## Verify and configure the existing tablet pages

PAL confirms that installed pages are selected by publishing
`pal_web_msgs/WebGoTo` on `/web/go_to` with
`type = WebGoTo.TOUCH_PAGE`. The visible project/page titles do not prove the
internal URIs, so the local sidecar leaves both identifiers empty and refuses
to publish until they are verified. It never guesses that `Main` or
`Zoek_de_verschillen` is the URI.

References: [PAL ARI touchscreen tutorial](https://docs.pal-robotics.com/ari/sdk/23.1/touchscreen/building-first-touchscreen-interaction.html),
[PAL `/web/go_to` reference](https://docs.pal-robotics.com/sdk/23.12/topics/web-go_to.html),
and [the official `WebGoTo.msg`](https://github.com/pal-robotics/pal_msgs/blob/f992ee7cfa5db79b92bd1b5a47d318b9caacc8bf/pal_web_msgs/msg/WebGoTo.msg).

Run these read-only checks on ARI:

```bash
rosmsg show pal_web_msgs/WebGoTo
python3 -c "from pal_web_msgs.msg import WebGoTo; print({n: getattr(WebGoTo, n) for n in ('IMAGE','VIDEO','URI','URL','TOUCH_PAGE','TOUCH_PROJECT')}); print(list(zip(WebGoTo.__slots__, WebGoTo._slot_types)))"
rostopic echo /web/go_to
```

While `rostopic echo` runs, use `http://ari-47c/touch_web` to display **Main**
and **Zoek_de_verschillen** and record the emitted `type` and exact `value`.
Also use the Touch Web Manager's **Copy URI** control. If necessary inspect the
feedback interface:

```bash
rosmsg show pal_web_msgs/TouchWeb
rostopic echo /touch_web_state
```

After confirming the two page URIs, place them at the beginning of
`ari/ari_social_proxy.py`:

```python
TABLET_MAIN_PAGE_URI = "verified-main-page-uri"
TABLET_GAME_PAGE_URI = "verified-game-page-uri"
```

Do not put display names there unless the live output proves they are the
actual URIs. Deploy the sidecar only after that edit. The code publishes once
at a phase boundary, uses the symbolic `WebGoTo.TOUCH_PAGE` constant, accepts
only the two logical targets, restores Main on chat/goodbye/reset/shutdown and
falls back to chat if the game page cannot be requested safely.

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

The result should report `ok: true`, sidecar version
`ari-social-v0.4.2-guardian-operator-emotes`, a two-second face greeting, and initial policy
`IDLE_SOCIAL`. `tablet_available` remains `false` until both verified internal
page URIs are configured.

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
mode, attention, movement and tablet control are disabled and expressions use
the existing RoboBOND proxy when possible. The game will not proceed as though
its pictures are visible when its page request fails.

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
requests Main when configured, publishes zero velocity, returns the face to
neutral, and requests `IDLE_SOCIAL`. Stop the separate RoboBOND proxy only when
it is no longer needed.

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
rostopic info /web/go_to
```

If an older demo (for example `/welcome_demo`) is also publishing expressions,
attention, or velocity, stop that demo through its normal launcher before
starting this one. Do not kill unfamiliar ROS nodes automatically.

## Static and mocked validation

The selected ARI conversation/social/game/operator/emote suites plus QT,
shared ASR and hub regressions pass: 257 tests, including 41 new operator,
emote and hub tests. Robot, audio and process interfaces are mocked. Python,
browser-script syntax and PowerShell launcher/deploy syntax are checked.
No robot, microphone, navigation or movement is run.
Full test discovery still encounters a pre-existing stale import in
`tests/test_mic.py` (`ari.llm_mic_test`, a module removed in an earlier commit).
The repository ignores older `/tests` files, but `.gitignore` explicitly
includes the three new suites `test_ari_operator.py`, `test_ari_emotes.py` and
`test_robot_hub.py` so these tests are visible in a normal handover.

Live verification remains necessary for the real PAL page URIs, their A/B
labels if used, the greeting wave, and semantic recognition of actual visitor
speech. The semantic regression tests mock the LLM and cannot establish its
real recognition accuracy.

## Operator controls and editable emotes

Both `http://127.0.0.1:8765` and the new laptop/iPad hub offer:

- **Begroet bezoeker**: enters the existing v0.4 introduction, with its wave
  and optional name question. It is available from standby, not mid-session.
- Typed speech (up to 400 characters), short preset phrases and arm gestures.
- Temporary face overrides and volume from 30 to 100 percent.
- JSON-defined emotes and a custom script field.
- **STOP acties**: cancels pending output/actions and stops speech, music and
  scripted movement. It retains the visitor and game progress.
- **Terug naar standby**: additionally clears the visitor/game and restores
  Main and the normal standby policy.

Automatic replies and operator/emote speech use one speech lock. Waiting
speech rechecks its cancellation/generation after acquiring the lock. An
operator action pauses ordinary listening while it runs; expected-response
prompts still use the existing early microphone overlap. Browser arrows are
ignored while typing in a text field.

`ari/emotes.json` is reloaded for each request. Edit its named lists to change
or add emotes without a restart. The available names refresh in both pages.
For example, a small custom script can contain:

```text
expression happy
gesture wave
say Goedendag, fijn dat u er bent.
wait 1
```

Supported steps are `head pan tilt seconds`, `turn degrees`, `drive meters`,
`gesture name`, `expression name`, `say text`, `music on/off` and
`wait seconds`. The parser checks all steps before starting. Limits: 128
steps, 12,000 script characters, 400 characters per spoken line, ten seconds
per wait, head pan -72..72 degrees, tilt -24..14 degrees, head duration
0.2..5 seconds, 180 degrees per turn, 0.5 m forward or 0.3 m backward per drive.

The supplied dance turns the base but does not drive forward/backward. Head
steps require the native head controller and `/gaze_manager/disable_neck`.
They pause PAL attention and restore it three seconds after the last head
movement, or immediately on completion/STOP/error. Without the pause service,
head steps fail visibly instead of competing with tracking. Default standby
smiles and speaking-only happiness remain automatic; only explicit operator
face/emote overrides temporarily take precedence.

Turns and drives use the existing low-speed velocity loop, emergency state,
ramping, obstacle checks and dead-man timeout. Odometry measures progress
when fresh; otherwise progress is estimated from the commanded ramped speed.
As in existing manual control, missing obstacle data does not block every
movement. Forward motion stops for a detected obstacle; backward motion has
no rear obstacle check. Test with a clear area and an operator. Arrow input
cancels an emote first; it resumes after cancellation cleanup completes.

Music uses `~/dance.wav` or `~/StillDRE.wav` on ARI if present. Otherwise the
sidecar generates its own short tune in `/tmp/ari_dance_tune.wav`. Playback
needs `paplay` or `aplay`; STOP/cleanup terminates the owned player process.
Volume uses PAL's `/pal/playback_volume` parameter. These audio behaviours
still need verification on the actual robot.

An arm gesture already submitted through RoboBOND may finish its native motion;
the existing interface provides no per-gesture cancellation. STOP prevents
later steps and stops the sidecar's base/head/music actions, but is not the
robot's physical emergency stop.

The VUmc guide (`--mode guardian`), its staff alerts and the handshake come
from the `ari-guardian-hub` branch and run beside the companion conversation;
the hub's card chooses between the two before ARI starts.

Before physical testing also inspect:

```bash
rosservice info /gaze_manager/disable_neck
rostopic info /head_controller/command
rostopic hz /mobile_base_controller/odom
rosparam get /pal/playback_volume
```

After deploying the sidecar with `ari_save_social.ps1`, run the whole operator
hub from the repository root using `.\venv\Scripts\python.exe -m hub` or
`start_hub.bat`. Start ARI from its card. A standalone ARI start remains
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ari\start_ari_demo.ps1`.

Files in this operator/hub extension: `ari/operator_ui.py`,
`ari/ari_mic_test.py`, `ari/social_control.py`, `ari/ari_social_proxy.py`,
`ari/emotes.py`, `ari/emotes.json`, both ARI PowerShell scripts, `hub/server.py`,
`hub/page.html`, `hub/__init__.py`, `hub/__main__.py`, `start_hub.bat`, the two
READMEs, `.gitignore` and the three new test files listed above. Existing
conversation/game files and the separate local QT changes are preserved.
