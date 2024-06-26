#! /usr/bin/env python3

import rospy, threading
import sys, select, termios, tty
from std_msgs.msg import Empty
from geometry_msgs.msg import Twist
from hector_uav_msgs.srv import EnableMotors
import actionlib
from hector_uav_msgs.msg import TakeoffAction, TakeoffGoal, LandingAction, LandingGoal

msg = """
Reading from the keyboard  and Publishing to Twist!
---------------------------
LEFT HAND:

  `w` : Up
  `s` : Down
  `a` : Yaw Left
  `d` : Yaw Right

---------------------------
RIGHT HAND:

  `i` : Forward
  `k` : Backward
  `j` : Left
  `l` : Right

---------------------------
TRIGGER:

  `=` : Take Off
  `-` : Land
  `Else Key` : Stop

   CTRL-C to quit
"""

move_bindings = {
    'w':( 0, 0, 1, 0),  # Up
    's':( 0, 0,-1, 0),  # Down
    'a':( 0, 0, 0, 1),  # Yaw Left
    'd':( 0, 0, 0,-1),  # Yaw Right

    'k':(-1, 0, 0, 0),  # Front
    'i':( 1, 0, 0, 0),  # Back
    'l':( 0,-1, 0, 0),  # Left
    'j':( 0, 1, 0, 0),  # Right
}

trigger_bindings = {
    '=': -1,  # Take off
    '-': -2,  # Land
}

class Publish_Threading(threading.Thread):
    def __init__(self, rate:float) -> None:
        super(Publish_Threading, self).__init__()
        self.publisher = rospy.Publisher('/cmd_vel', Twist, queue_size=1)

        self.x, self.y, self.z, self.theta, self.speed = 0.0, 0.0, 0.0, 0.0, 0.0
        self.condition = threading.Condition()
        self.flag = False

        if rate != 0.0:
            self.time_out = 1.0 / rate
        else: 
            self.time_out = None
            # Set time out to be None as the rate is 0
            # Avoid unlimited waiting for new data to publish

        self.start()

    def wait_for_subscriber(self) -> None:
        """ Waiting for subscriber to subscribe the topics """
        i = 0
        while not rospy.is_shutdown() and self.publisher.get_num_connections() == 0:
            if i == 4:
                rospy.loginfo('Waiting for subscriber to connect to {}'.format(self.publisher.name))
                rospy.sleep(0.5)
                i += 1
                i %= 5

        if rospy.is_shutdown():
            rospy.logerr('Got shutdown request before subscribers connected')

    def update(self, x:float, y:float, z:float, theta:float, speed:float) -> None:
        """
        Update the command value.
        @param x: x linear velocity
        @param y: y linear velocity
        @param z: z linear velocity
        @param theta: yaw angle velocity
        @param speed: the scale of each velocity
        """
        self.condition.acquire()
        self.x, self.y, self.z, self.theta, self.speed = x, y, z, theta, speed
        self.condition.notify() # Notify publish thread that a new message obtained
        self.condition.release()

    def run(self) -> None:
        twist = Twist()
        while not self.flag:
            self.condition.acquire()
            self.condition.wait(self.time_out) # Wait for a new message or timeout

            # Twist message
            twist.linear.x = self.x * self.speed
            twist.linear.y = self.y * self.speed
            twist.linear.z = self.z * self.speed
            twist.angular.x, twist.angular.y = 0, 0
            twist.angular.z = self.theta

            self.condition.release()

            # Publish
            self.publisher.publish(twist)
        
        # Publish stop message as the thread out
        twist.linear.x, twist.linear.y, twist.linear.z = 0, 0, 0
        twist.angular.x, twist.angular.y, twist.angular.z = 0, 0, 0
        self.publisher.publish(twist)

    def stop(self) -> None:
        """ Stop the drone """
        self.flag = True
        self.update(0.0, 0.0, 0.0, 0.0, 0.0)
        self.join()

    @staticmethod
    def getKey(settings:list, timeout) -> str:
        """
        Capture key value from keyboard
        @param setting: system setup
        @param timeout: the time out duration
        @return: key value in string format
        @rtype: str
        """
        tty.setraw(sys.stdin.fileno())
        rlist, _, _ = select.select([sys.stdin], [], [], timeout)
        if rlist:
            key = sys.stdin.read(1)
        else:
            key = ''
    
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        return key 
   


if __name__ == "__main__":
    settings = termios.tcgetattr(sys.stdin)
    
    rospy.init_node('keyboard_teleop_node')
    print(msg)

    # Get paramter sever
    speed = float(rospy.get_param('~speed', 0.2))
    repeat = float(rospy.get_param('~repeat_rate', 0.0))
    timeout = float(rospy.get_param('~key_timeout', 0.0))
    if timeout == 0.0:
        timeout = None

    # Service proxy for enabling motors
    rospy.wait_for_service('/enable_motors')
    try:
        enable_motors = rospy.ServiceProxy('/enable_motors', EnableMotors)
        response = enable_motors(True)
        if response.success:
            rospy.loginfo("Motors enabled successfully")
        else:
            rospy.logerr("Failed to enable motors: %s", response.message)
    except rospy.ServiceException as e:
        rospy.logerr("Service call failed: %s", str(e))
        sys.exit(1)
        
    # Action clients for takeoff and landing
    takeoff_client = actionlib.SimpleActionClient('action/takeoff', TakeoffAction)
    landing_client = actionlib.SimpleActionClient('action/landing', LandingAction)
    takeoff_client.wait_for_server()
    landing_client.wait_for_server()


    publisher_thread = Publish_Threading(repeat)
    x, y, z, theta = 0, 0, 0, 0

    try:
        publisher_thread.wait_for_subscriber()
        publisher_thread.update(x, y, z, theta, speed)
        while True:
            key = Publish_Threading.getKey(settings, timeout)
            if key in move_bindings.keys():
                x = move_bindings[key][0]
                y = move_bindings[key][1]
                z = move_bindings[key][2]
                theta = move_bindings[key][3]

            elif key in trigger_bindings.keys():
                if trigger_bindings[key] == -1: # + take off
                    rospy.loginfo('Take off ...')
                    takeoff_goal = TakeoffGoal()
                    takeoff_client.send_goal(takeoff_goal)
                    takeoff_client.wait_for_result()
                    if takeoff_client.get_state() == actionlib.GoalStatus.SUCCEEDED:
                        rospy.loginfo("Takeoff succeeded")
                    else:
                        rospy.logwarn("Takeoff failed")

                elif trigger_bindings[key] == -2: # - land
                    rospy.loginfo('Landing ...')
                    landing_goal = LandingGoal()
                    landing_client.send_goal(landing_goal)
                    landing_client.wait_for_result()
                    if landing_client.get_state() == actionlib.GoalStatus.SUCCEEDED:
                        rospy.loginfo("Landing succeeded")
                    else:
                        rospy.logwarn("Landing failed")
            
            else:
                # Skip updating if key timeout and drone already stopped.
                if key == '' and x == 0 and y == 0 and z == 0 and theta == 0:
                    continue

                x, y, z, theta = 0, 0, 0, 0
                if key == '\x03': # <ctrl-c> ascii for exit
                    break
            
            publisher_thread.update(x, y, z, theta, speed)
            rospy.loginfo('x:{} y:{} z:{} theta:{}'.format(x, y, z, theta))

            # Clear the terminal and print the message again
            print("\033[H\033[J")
            print(msg)
            print(f'Current command: x:{x} y:{y} z:{z} theta:{theta}')
    
    except Exception as exception:
        rospy.logerr(exception)

    finally:
        publisher_thread.stop()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)

