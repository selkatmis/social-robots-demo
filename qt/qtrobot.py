from robond.core.listener_backend import ListenerBackend
from robond.robot.remote import RemoteRobotInterface
from robond.core.robot_interface import Expression, Gesture

QT_IP = "10.96.21.245"
QT_PORT = 7342


class TestListener(ListenerBackend):
    """Temporary listener. Speech recognition comes later."""

    def listen(self, timeout=10, silence_timeout=None):
        return "[TIMEOUT]"


def main():
    print("Connecting to QT...")

    robot = RemoteRobotInterface(
        host=QT_IP,
        port=QT_PORT,
        listener=TestListener()
    )

    print("Successfully connected to QT!")

    gestures = robot.get_available_gestures()
    print(gestures)
    robot.set_expression(Expression.HAPPY)
    
    print("Expressions:", list(Expression))

    print("Facial methods:", [
        name for name in dir(robot)
        if "express" in name.lower() or "emotion" in name.lower()
    ])
    robot.say_with_gesture(
    "Goedemorgen! Wat leuk dat u er bent!",
    Gesture.WAVE
)
    print("\nDemonstration finished!")
    

if __name__ == "__main__":

    main()