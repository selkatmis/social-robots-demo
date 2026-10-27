start demo:
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\qt\start_qt_demo.ps1
```

## Greeting wave and happy face

When visitor speech wakes QT from standby, the OFF-to-ON demo transition now
calls the native `/qt_robot/emotion/show` service with `QT/happy` and the native
`/qt_robot/gesture/play` service with the configured greeting gesture. It
greets once per session. The happy expression lasts three seconds, then normal
expression handling resumes. Face tracking continues while the blocking gesture
service runs on a separate thread. The existing RoboBOND proxy is unchanged.

Settings are at the beginning of `qt/stand.py`: `GREETING_GESTURE_NAME`
(initially `QT/hi`), `GREETING_GESTURE_SPEED` and `GREETING_HAPPY_SECONDS`.
The installed gesture service type is discovered through ROS at runtime.
A missing/rejected gesture logs a warning and leaves the conversation running.

Before physical testing, check the installed greeting name and service on QT:

```bash
rosservice type /qt_robot/gesture/play
rossrv show "$(rosservice type /qt_robot/gesture/play)"
```

Check the gesture names in the Educator app or QTRP's `~/robot/data/gestures`
folder. `QT/hi` is configurable and has not been verified on this QTPC.
Also check whether that recorded gesture moves the head as well as the arm;
native gestures can include head motion, which needs physical verification
alongside face tracking.

Save the updated robot-side tracker once, then start the demo normally:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\qt\qt_save_stand.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\qt\start_qt_demo.ps1
```

The tracker/launcher/deploy version is `demo-face-grace-12s-greeting-v2`.
ROS service contracts: [LuxAI native ROS service documentation](https://docs.luxai.com/docs/tutorials/python/python_ros_services).
