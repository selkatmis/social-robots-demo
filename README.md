# Social Robots Demo — Nieuwspoort 2026

Interactive social-robot demonstrations for visitors at **Nieuwspoort on 27 October 2026**, developed with students and researchers at **Vrije Universiteit Amsterdam**.

We are preparing three short demonstrations in **Dutch**:

| Robot | Demonstration | What visitors experience |
| --- | --- | --- |
| **ARI** | Healthcare | A supportive conversation and an appropriate handover to a caregiver. |
| **QT** | Education | A mini tutoring session with feedback and hints. |
| **Furhat** | Research information | Questions about social robotics, the *Ik ben Alice* project, and current VU research. |

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
