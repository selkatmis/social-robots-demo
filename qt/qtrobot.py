from robond.core.robot_interface import Expression, Gesture  # noqa: E402
from robond.robot.remote import RemoteRobotInterface


robot = RemoteRobotInterface(host="ip-address", port=7342, listener=None)