# Social Robots Demo — Nieuwspoort 2026

Interactive social-robot demonstrations for visitors at **Nieuwspoort on 27 October 2026**, developed with students and researchers at **Vrije Universiteit Amsterdam**.

We are preparing three short demonstrations in **Dutch**:

| Robot | Demonstration | What visitors experience |
| --- | --- | --- |
| **ARI** | Healthcare | A supportive conversation and an appropriate handover to a caregiver. |
| **QT** | Education | A mini tutoring session with feedback and hints. |
| **Furhat** | Introduction | A welcome: who we are, our FOAF project on ARI, and where to go next (ARI and QT). |

**Our story:** *Van Alice naar een nieuwe generatie sociale robots.* The stand connects the *Ik ben Alice* documentary (2015) to what current social robots can demonstrate, and to the continuing importance of education and scientific research.

**Priority until 27 October:** Three short, reliable, Dutch-speaking demonstrations that clearly communicate the progression from *Ik ben Alice* to current social-robotics research.

## 1. Folder structure

```text
social_robots_demo/
├── ari/
│   ├── __init__.py
│   ├── ari.py               # ARI-specific code
│   └── llm_test.py          # Test the shared LLM from ARI's folder
├── furhat/
│   ├── __init__.py
│   ├── furhat.py            # Furhat-specific code
│   └── llm_test.py
├── qt/
│   ├── __init__.py
│   ├── qtrobot.py           # QT-specific code
│   └── llm_test.py
├── shared/
│   ├── __init__.py
│   ├── llm.py               # Shared Mistral API wrapper
│   ├── llm_old.py           # Older implementation, for reference
│   └── .env                 # Your local API key — NEVER commit
├── wheels/                  # Local robot package dependencies
├── venv/                    # Local Python environment — NEVER commit
├── requirements.txt
└── README.md
```

**The rule:** Keep robot-specific behavior in its robot's folder. Put code that several robots need (such as calling the LLM) in `shared/`. 

## 2. First-time setup

You need Python, VS Code (recommended), and a copy of this repository. The examples below use Windows, but the Python commands are the same on other systems.

#### Step 1 — Create and activate a virtual environment:

**Creating virtual environment:**

```powershell
python -m venv venv
```

**Activating the virtual environment:**

For windows:
```powershell
.\venv\Scripts\Activate.ps1
```

For macOS:
```powershell
source venv/bin/activate
```

**Step 2 — Install dependencies:**

```powershell
python -m pip install -r requirements.txt
```

**Step 4 — Configure your own API key:**

At each robot's folder, you can find `llm_test.py`. These are for testing out prompts, and you can print the responses you get from the LLM to test them before implementing it into the robot.

### Testing without the robot

From the **project root**, run any of the tests:

```powershell
python -m ari.llm_test
python -m qt.llm_test
python -m furhat.llm_test
```

### Talking to Furhat

1. Fill in `shared/.env`: `MISTRAL_API_KEY`, `FURHAT_HOST` (the robot's IP) and, only if one is set, `FURHAT_AUTH_KEY`.
2. Open the robot's IP in a browser, click **Real-time API** and start it. Your laptop must be on the same network as the robot.
3. From the **project root**, run:

```powershell
python -m furhat.furhat
```

Furhat greets you and then listens with its own microphone (Dutch). Press `Ctrl+C` to stop.

No robot nearby? Only `MISTRAL_API_KEY` is needed for these:

```powershell
python -m furhat.furhat --mic    # talk with your laptop's microphone; the reply is spoken (macOS)
python -m furhat.furhat --text   # type instead of talking
``` The persona and rules are in `SYSTEM_PROMPT` at the top of `furhat/furhat.py`.

## 3. Robot hub: laptop and iPad control

From this repository on the Windows laptop, start the hub with:

```powershell
.\venv\Scripts\python.exe -m hub
```

Alternatively, double-click `start_hub.bat`. The hub prints the laptop's LAN
address, for example `http://192.168.1.20:8080`. Open it on an iPad on the same
Wi-Fi, or use `http://127.0.0.1:8080` on the laptop. Safari's **Add to Home
Screen** can keep it available as a full-screen operator page. Allow the
laptop's Python through the firewall on the intended private network if needed.
The hub has no login; access is for operators on that trusted network.

Each robot has Start, Stop, status and a bounded log. The Windows commands are
the existing `ari/start_ari_demo.ps1`, `qt/start_qt_demo.ps1` and
`python -m furhat.furhat_test`. The hub adds no conversation engine and does
not share visitor memory between robots. ARI's microphone remains on the
laptop. Its existing local interface remains on port `8765`.

The ARI panel shows the transcript, phase and difference-game progress, plus
typed speech, preset phrases, greeting, faces, gestures, emotes, volume,
press-and-hold movement, STOP and return to standby. Deploy the new ARI
sidecar once before first use:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\ari\ari_save_social.ps1
.\venv\Scripts\python.exe -m hub
```

Starting the hub itself does not deploy files or start any robot. A sidecar
update can also be requested explicitly from ARI's card after a version error.
See [the ARI operator/emote instructions](ari/README.md) for details and the
robot-side checks that still require physical testing.

Stop a robot using its card. The hub first requests the normal Ctrl+C shutdown
and waits up to 45 seconds. A second Stop requests forced termination. Forced
stops and failures appear in the card and log. Ctrl+C in the hub window stops
all processes it started. Per-robot Windows jobs also clean up local helper
processes after a launcher exits. Abrupt window closure or forced termination
can skip the scripts' remote cleanup: check ARI/QTPC services afterwards.
An ARI demo started separately is shown as external; stop it in its own window.
