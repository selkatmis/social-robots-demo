from robond.core.listener_backend import ListenerBackend
from robond.robot.furhat import FurhatRobotInterface


FURHAT_IP = "10.96.21.46"


class TestListener(ListenerBackend):
    """Temporary listener for testing the robot connection."""

    def listen(self, timeout=10, silence_timeout=None):
        return "[TIMEOUT]"


def main():
    print(f"Connecting to Furhat at {FURHAT_IP}...")

    robot = FurhatRobotInterface(
        host=FURHAT_IP,
        auth_key="furhat_key",
        listener=TestListener(),
        tts_lang="nl-NL",
        connect_timeout=15.0
    )

    print("Successfully connected to Furhat!")

    # Discover available speech methods in this robond version
    methods = [
        name for name in dir(robot)
        if any(word in name.lower()
               for word in ("speak", "say", "listen", "stop", "close"))
    ]

    print("Available methods:", methods)

    input("Press Enter to exit...")


if __name__ == "__main__":
    main()