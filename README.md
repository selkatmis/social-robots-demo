# Social Robots Demo — Nieuwspoort 2026

Interactive social-robot demonstrations for visitors at **Nieuwspoort on 27 October 2026**, developed with students and researchers at **Vrije Universiteit Amsterdam**.

We are preparing three short demonstrations in **Dutch**:

| Robot | Demonstration | What visitors experience |
| --- | --- | --- |
| **ARI** | Healthcare | ARI as the guide in the VUmc outpatient hall: it helps older visitors (also with dementia) find their way and hands over to a caregiver when needed. |
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
├── hub/
│   ├── server.py            # Robot hub: start the robots and drive ARI from an iPad
│   └── page.html            # The iPad page (black and white, Ubuntu font)
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
```

The persona and rules are in `SYSTEM_PROMPT` at the top of `furhat/furhat.py`.

## 3. Robot hub: everything from an iPad

The hub is one page with a button per robot (ARI, QT, Furhat) that starts or
stops its demo with one tap, plus everything to drive ARI: the conversation
as it happens, staff alerts, faces, gestures, a handshake, the dance and other
emotes, typed lines for ARI to say, the arrow pad and volume.

On the demo laptop, double-click `start_hub.bat`, or from the project root:

```powershell
python -m hub
```

It prints the address to open on the iPad, for example
`http://192.168.1.20:8080`. The iPad must be on the same wifi as the laptop.
In Safari, tap Share and **Add to Home Screen** to open it full screen like an
app. Windows asks once to let Python through the firewall; allow it for
private networks.

Each robot runs exactly as when you start it by hand: ARI and QT through their
start scripts, Furhat with `python -m furhat.furhat_test`. Stopping from the
iPad is the same as pressing Ctrl+C in its window, so the robots still shut
down cleanly. Ctrl+C in the hub's own window stops the hub and every robot it
started. The **Log** button on a card shows that robot's output.

ARI's card has a switch between **Gids VUmc** (the guide, default) and
**Gesprek** (the open conversation demo). The first time ARI starts after this
update, its card offers **Sidecar bijwerken**, which runs
`ari\ari_save_social.ps1` for you; then press **Start ARI** again.
