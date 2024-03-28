
"""
PybulletRobot
~~~~~~~~~~~~~
"""

import sys
import os
import yaml
import pickle
import time
import datetime

import numpy as np
import scipy as sp
import matplotlib.pyplot as plt
import pybullet as p

from ..utils import *

JOINT_SAFETY_FACTOR = 0.95


def get_subdirectories(path):
    return [os.path.basename(f.path) for f in os.scandir(path) if f.is_dir()]


class PybulletRobot:
    """Pybullet Simulator Robot Class

    :param int ClientId: pybullet GUI client's ID
    :param dict[] robot_info: dictionary of robot's information
    :param float dt: simulation time step
    """
    def __init__(self, ClientId, robot_info, dt):

        # Simulator configuration
        self.__filepath = os.path.dirname(os.path.abspath(__file__))
        self.__urdfpath = self.__filepath + "/../assets/urdf"

        self.ClientId = ClientId
        self._robot_info = robot_info
        self.dt = dt

        self._initialization()

    def robot_update(self):
        """
        Update the state of the robot by implementing _pre_robot_update, _get_robot_states, _compute_torque_input,
        _control_robot, and _post_robot_update methods.
        """

        self._pre_robot_update()      # pre update

        self._get_robot_states()      # update robot's states
        self._compute_torque_input()  # compute applied motor torques
        self._control_robot()         # apply motor torques

        self._post_robot_update()     # post update

    def _pre_robot_update(self):
        """
        protected method used by robot_update.
        """
        pass

    def _post_robot_update(self):
        """
        protected method used by robot_update.
        """

        if self._is_constraint_visualization:
            self._constraint_visualizer()


    def _initialization(self):
        """
        Protected method _initialize sets all data to None, False, or emptiness. Then finally this method loads robot by
        implementing protected method _load_robot().
        """

        # Load robot
        self._load_robot()


    def _load_robot(self):
        """
        This method is protected method of pybulletRobot class which load robot's information from yaml file

        :return:
        """

        # Get robot configuration
        self._robot_name = self._robot_info["robot_name"]              # robot name
        self._base_pos = self._robot_info["robot_position"]            # base position
        base_eul = self._robot_info["robot_orientation"]               # base orientation (euler XYZ, degree)
        self._base_quat = eul2quat(base_eul, 'XYZ', degree=True)       # base orientation (quaternion)
        self._base_SE3 = xyzquat2SE3(self._base_pos, self._base_quat)  # base SE3

        self._is_joint_limit = self._robot_info["robot_properties"]["joint_limit"]
        self._is_constraint_visualization = self._robot_info["robot_properties"]["constraint_visualization"]

        available_robot_types = get_subdirectories(self.__urdfpath)
        load_success = False
        for robot_type in available_robot_types:
            available_robot_names = get_subdirectories(self.__urdfpath + "/{}".format(robot_type))
            if self.robot_name in available_robot_names:
                self._robot_type = robot_type  # robot type
                load_success = True
                break

        if not load_success:
            PRINT_RED("*** NO AVAILABLE ROBOT ***")
            PRINT_BLACK("Robot name", self.robot_name)
            return

        try:
            # Open YAML file
            with open(self.__urdfpath + "/{}/robot_configs.yaml".format(self._robot_type)) as yaml_file:
                self._robot_configs = yaml.load(yaml_file, Loader=yaml.FullLoader)
        except:
            PRINT_RED("*** FAILED TO LOAD ROBOT CONFIGS ***")
            PRINT_BLACK("Robot name", self.robot_name)
            PRINT_BLACK("Robot type", self.robot_type)
            return

        # Import robot
        flags = p.URDF_USE_INERTIA_FROM_FILE + p.URDF_USE_SELF_COLLISION + p.URDF_USE_SELF_COLLISION_EXCLUDE_PARENT
        urdf_dir = self.__urdfpath + "/{0}/{1}".format(self.robot_type, self.robot_name)
        urdf_path = urdf_dir + "/model.urdf"

        self.robotId = p.loadURDF(urdf_path, basePosition=self._base_pos, baseOrientation=self._base_quat,
                                  flags=flags, physicsClientId=self.ClientId)


        # Get robot's info from robot_info.yaml file
        self.RobotBaseJointIdx = self._robot_configs[self.robot_name]["JointInfo"]["RobotBaseJoint"]
        self.RobotMovableJointIdx = self._robot_configs[self.robot_name]["JointInfo"]["RobotMovableJoint"]
        self.RobotEEJointIdx = self._robot_configs[self.robot_name]["JointInfo"]["RobotEEJoint"]

        if len(self.RobotBaseJointIdx) == 0:
            self.RobotBaseJointIdx = [-1]
        if len(self.RobotEEJointIdx) == 0:
            self.RobotEEJointIdx = [self.RobotMovableJointIdx[-1]]

        state = p.getBasePositionAndOrientation(self.robotId, physicsClientId=self.ClientId)
        T_wr = xyzquat2SE3(state[0], state[1])
        if self.RobotBaseJointIdx[0] == -1:
            state = p.getBasePositionAndOrientation(self.robotId, physicsClientId=self.ClientId)
        else:
            state = p.getLinkState(self.robotId, self.RobotBaseJointIdx[0], physicsClientId=self.ClientId)
        T_wb = xyzquat2SE3(state[0], state[1])

        self._T_rb = TransInv(T_wr) @ T_wb  # pose of robot's base frame in robot's root frame

        # Get pinocchio model to compute robot's dynamics & kinematics
        self.pinModel = PinocchioModel(urdf_dir, self._base_SE3 @ self._T_rb)

        # set robot's number of bodies and number of joints
        self._numBodies = 1 + p.getNumJoints(self.robotId, self.ClientId)
        self._numJoints = len(self.RobotMovableJointIdx)

        self._GraspObjectId = None

        if self._is_joint_limit is False:
            for idx in self.RobotMovableJointIdx:
                p.changeDynamics(self.robotId, idx, jointLowerLimit=-314, jointUpperLimit=314,
                                 physicsClientId=self.ClientId)

        # Get robot color
        self._robot_color = [None] * self.numBodies
        visual_data = p.getVisualShapeData(self.robotId)
        for data in visual_data:
            self._robot_color[data[1]+1] = data[7]

        self._initialize_robot()

        PRINT_BLUE("******** ROBOT INFO ********")
        PRINT_BLACK("Robot name", self.robot_name)
        PRINT_BLACK("Robot type", self.robot_type)
        PRINT_BLACK("DOF", self.numJoints)
        PRINT_BLACK("Joint limit", self._is_joint_limit)
        PRINT_BLACK("Constraint visualization", self._is_constraint_visualization)

    @property
    def robot_name(self):
        return self._robot_name

    @property
    def robot_type(self):
        return self._robot_type

    @property
    def base_pos(self):
        return self._base_pos[:]

    @property
    def base_quat(self):
        return self._base_quat[:]

    @property
    def base_SE3(self):
        return self._base_SE3.copy()

    @property
    def numJoints(self):
        return self._numJoints

    @property
    def numBodies(self):
        return self._numBodies

    # Get robot's information
    def _initialize_robot(self):

        # Robot's states
        self._q = np.zeros([self.numJoints, 1])        # position of joints (rad)
        self._qdot = np.zeros([self.numJoints, 1])     # velocity of joints (rad/s)
        self._qddot = np.zeros([self.numJoints, 1])    # acceleration of joints (rad/s^2)

        self._q_des = np.zeros([self.numJoints, 1])      # desired position of joints
        self._qdot_des = np.zeros([self.numJoints, 1])   # desired velocity of joints
        self._qddot_des = np.zeros([self.numJoints, 1])  # desired acceleration of joints

        self._Js = np.zeros([6, self.numJoints])      # Spatial jacobian matrix.
        self._Jb = np.zeros([6, self.numJoints])      # Body jacobian matrix.
        self._Jsinv = np.zeros([self.numJoints, 6])   # Inverse of spatial jacobian matrix
        self._Jbinv = np.zeros([self.numJoints, 6])   # Inverse of body jacobian matrix
        self._Jsdot = np.zeros([6, self.numJoints])   # Time-derivative of spatial jacobian matrix
        self._Jbdot = np.zeros([6, self.numJoints])   # Time-derivative of body jacobian matrix

        self._M = np.zeros([self.numJoints, self.numJoints])  # Mass matrix of robot
        self._C = np.zeros([self.numJoints, self.numJoints])  # Coriolis matrix of robot
        self._c = np.zeros([self.numJoints, 1])               # Coriolis vector of robot (c = C@qdot)
        self._g = np.zeros([self.numJoints, 1])               # Gravity vector of robot
        self._tau = np.zeros([self.numJoints, 1])             # Input torque (N*m)

        self._p = np.zeros([6, 1])      # End-effector's pose (xyz, xi_dot)
        self._T_end = np.zeros([4, 4])   # End-effector's pose in SE3

        # Constraint & flag
        self._jointpos_lower = [0 for _ in range(self.numJoints)]
        self._jointpos_upper = [0 for _ in range(self.numJoints)]
        self._jointvel = [0 for _ in range(self.numJoints)]
        self._jointforce = [0 for _ in range(self.numJoints)]

        self._jointpos_flag = [0 for _ in range(self.numJoints)]
        self._jointvel_flag = [0 for _ in range(self.numJoints)]
        self._jointforce_flag = [0 for _ in range(self.numJoints)]
        self._collision_flag = [0 for _ in range(self.numBodies)]

        # Get joint constraints
        for idx in range(self.numJoints):
            jointInfo = p.getJointInfo(bodyUniqueId=self.robotId, jointIndex=self.RobotMovableJointIdx[idx],
                                       physicsClientId=self.ClientId)
            self._jointpos_lower[idx] = jointInfo[8] * JOINT_SAFETY_FACTOR
            self._jointpos_upper[idx] = jointInfo[9] * JOINT_SAFETY_FACTOR
            self._jointvel[idx] = jointInfo[10] * JOINT_SAFETY_FACTOR
            self._jointforce[idx] = jointInfo[11] * JOINT_SAFETY_FACTOR

        # Add end-effector
        visualShapeId = p.createVisualShape(shapeType=p.GEOM_SPHERE, radius=0.01, rgbaColor=[0, 1, 0, 0.7],
                                            physicsClientId=self.ClientId)

        self._endID = p.createMultiBody(baseVisualShapeIndex=visualShapeId, basePosition=[0, 0, 0],
                                        baseOrientation=[0, 0, 0 ,1], physicsClientId=self.ClientId)

        self._endID_x = p.addUserDebugLine(lineFromXYZ=[0, 0, 0], lineToXYZ=[0.05, 0, 0], lineColorRGB=[1, 0, 0],
                                           lineWidth=2, parentObjectUniqueId=self._endID, physicsClientId=self.ClientId)
        self._endID_y = p.addUserDebugLine(lineFromXYZ=[0, 0, 0], lineToXYZ=[0, 0.05, 0], lineColorRGB=[0, 1, 0],
                                           lineWidth=2, parentObjectUniqueId=self._endID, physicsClientId=self.ClientId)
        self._endID_z = p.addUserDebugLine(lineFromXYZ=[0, 0, 0], lineToXYZ=[0, 0, 0.05], lineColorRGB=[0, 0, 1],
                                           lineWidth=2, parentObjectUniqueId=self._endID, physicsClientId=self.ClientId)

        # Remove the PyBullet's built-in position controller's effect
        p.setJointMotorControlArray(bodyUniqueId=self.robotId,
                                    jointIndices=self.RobotMovableJointIdx,
                                    controlMode=p.POSITION_CONTROL,
                                    targetPositions=[0] * self.numJoints,
                                    forces=[0] * self.numJoints,
                                    physicsClientId=self.ClientId
                                    )


    def _get_robot_states(self):

        for i, idx in enumerate(self.RobotMovableJointIdx):
            states = p.getJointState(self.robotId, idx, physicsClientId=self.ClientId)

            self._q[i, 0] = states[0]     # q
            self._qdot[i, 0] = states[1]  # qdot
            self._qddot[i, 0] = 0         # TODO

        self._T_end = self.pinModel.FK(self._q)

        self._Js = self.pinModel.Js(self._q)
        self._Jb = self.pinModel.Jb(self._q)
        self._Jsdot = np.zeros([6, self.numJoints])  # TODO
        self._Jbdot = np.zeros([6, self.numJoints])  # TODO
        self._Jsinv = np.linalg.pinv(self._Js)
        self._Jbinv = np.linalg.pinv(self._Jb)

        self._M = self.pinModel.M(self._q)
        self._C = self.pinModel.C(self._q, self.qdot)
        self._c = self._C @ self._qdot
        self._g = self.pinModel.g(self._q)

        self._p = SE32PoseVec(self._T_end)

        self._update_base_pose()

        p.resetBasePositionAndOrientation(bodyUniqueId=self._endID, posObj=self._p[0:3, 0],
                                          ornObj=Rot2quat(self._T_end[0:3, 0:3]), physicsClientId=self.ClientId)
        
    def _update_base_pose(self):

        state = p.getBasePositionAndOrientation(self.robotId, physicsClientId=self.ClientId)

        self._base_pos = state[0]
        self._base_quat = state[-1]
        self._base_SE3 = xyzquat2SE3(self._base_pos, self._base_quat)
        self.pinModel.reset_base(self._base_SE3 @ self._T_rb)

    def _compute_torque_input(self):

        if True:
            Kp = 5000
            Kd = 200

            # Kp = 8000
            # Kd = 400

            qddot = self._qddot_des + Kp * (self._q_des - self._q) + Kd * (self._qdot_des - self._qdot)

            tau = self._M @ qddot + self._c + self._g

            self._tau = tau

        else:
            self._tau = self._g

    def _control_robot(self):

        p.setJointMotorControlArray(bodyUniqueId=self.robotId, jointIndices=self.RobotMovableJointIdx,
                                    controlMode=p.TORQUE_CONTROL, forces=self._tau.reshape([self.numJoints]),
                                    physicsClientId=self.ClientId)

    @property
    def q(self):
        return self._q.copy()

    @property
    def qdot(self):
        return self._qdot.copy()

    @property
    def qddot(self):
        return self._qddot.copy()

    @property
    def q_des(self):
        return self._q_des.copy()

    @property
    def p(self):
        return self._p.copy()

    @property
    def T_end(self):
        return self._T_end.copy()

    @property
    def tau(self):
        return self._tau.copy()

    @property
    def Js(self):
        return self._Js.copy()

    @property
    def Jb(self):
        return self._Jb.copy()

    def JsInv(self):
        return self._Jsinv.copy()

    def Jbinv(self):
        return self._Jbinv.copy()

    @property
    def M(self):
        return self._M.copy()

    @property
    def C(self):
        return self._C.copy()

    @property
    def c(self):
        return self._c.copy()

    @property
    def g(self):
        return self._g.copy()

    @property
    def q_lower(self):
        return self._jointpos_lower.copy()

    @property
    def q_upper(self):
        return self._jointpos_upper.copy()

    # Utils
    def _constraint_check(self):
        """
        This method provide functions for check constraint.
        It can check three kinds of limits, joint's position limits, joint's velocity limits, and collision.
        If constraints aren't kept, the method will change the value of _jointpos_flag of each joint.
        """

        # Joint position limit check
        for idx in range(self.numJoints):

            q = self._q[idx, 0] # current position of joint
            ql = self._jointpos_lower[idx]
            qu = self._jointpos_upper[idx]

            if q < ql or q > qu:
                if self._jointpos_flag[idx] == 0 or self._jointpos_flag[idx] == 1:
                    self._jointpos_flag[idx] = 2
                else:
                    self._jointpos_flag[idx] = 3
            else:
                if self._jointpos_flag[idx] == 2 or self._jointpos_flag[idx] == 3:
                    self._jointpos_flag[idx] = 0
                else:
                    self._jointpos_flag[idx] = 1

        # Joint velocity limit check
        for idx in range(self.numJoints):

            qdot = self._qdot[idx, 0]

            if np.abs(qdot) > self._jointvel[idx]:
                if self._jointvel_flag[idx] == 0 or self._jointvel_flag[idx] == 1:
                    self._jointvel_flag[idx] = 2
                else:
                    self._jointvel_flag[idx] = 3
            else:
                if self._jointvel_flag[idx] == 2 or self._jointvel_flag[idx] == 3:
                    self._jointvel_flag[idx] = 0
                else:
                    self._jointvel_flag[idx] = 1

        # Collision check
        for idx in range(self.numBodies):
            _contact_points_info = p.getContactPoints(bodyA=self.robotId, linkIndexA=idx-1,
                                                        physicsClientId=self.ClientId)
            if len(_contact_points_info) != 0:
                if self._collision_flag[idx] == 0 or self._collision_flag[idx] == 1:
                    self._collision_flag[idx] = 2
                else:
                    self._collision_flag[idx] = 3
            else:
                if self._collision_flag[idx] == 2 or self._collision_flag[idx] == 3:
                    self._collision_flag[idx] = 0
                else:
                    self._collision_flag[idx] = 1

    def _constraint_visualizer(self):
        """
        This method can display constraints of robot. If the constraints of robot aren't kept, the joint will be
        shown in different color. The method distinguish if the constraints of robot are kept or not with joint
        flags.
        """
        self._constraint_check()

        # Joint position limit check
        for i, idx in enumerate(self.RobotMovableJointIdx):
            if self._jointpos_flag[i] == 2:
                p.changeVisualShape(objectUniqueId=self.robotId, linkIndex=idx, rgbaColor=[0, 0.7, 0, 1],
                                    physicsClientId=self.ClientId)
            elif self._jointpos_flag[i] == 0:
                p.changeVisualShape(objectUniqueId=self.robotId, linkIndex=idx, rgbaColor=self._robot_color[idx],
                                    physicsClientId=self.ClientId)
                
        # Joint velocity limit check
        for i, idx in enumerate(self.RobotMovableJointIdx):

            if self._jointvel_flag[i] == 2:
                p.changeVisualShape(objectUniqueId=self.robotId, linkIndex=idx, rgbaColor=[0, 0, 0.7, 1],
                                    physicsClientId=self.ClientId)
            elif self._jointvel_flag[i] == 0:
                p.changeVisualShape(objectUniqueId=self.robotId, linkIndex=idx, rgbaColor=self._robot_color[idx],
                                    physicsClientId=self.ClientId)
                
        # Collision check
        for idx in range(self.numBodies):

            if self._collision_flag[idx] == 2:
                p.changeVisualShape(objectUniqueId=self.robotId, linkIndex=idx-1, rgbaColor=[0.7, 0, 0, 1],
                                    physicsClientId=self.ClientId)
            elif self._collision_flag[idx] == 0:
                p.changeVisualShape(objectUniqueId=self.robotId, linkIndex=idx-1, rgbaColor=self._robot_color[idx],
                                    physicsClientId=self.ClientId)

    ### Kinematics utils
    def body_jacobian(self, q, degree=False):
        if degree:
            q = np.asarray(q).reshape([self.numJoints, 1]) * np.pi/180
        else:
            q = np.asarray(q).reshape([self.numJoints, 1])

        return self.pinModel.Jb(q)

    def forward_kinematics(self, q, degree=False):
        if degree:
            q = np.asarray(q).reshape([self.numJoints, 1]) * np.pi/180
        else:
            q = np.asarray(q).reshape([self.numJoints, 1])

        return self.pinModel.FK(q)

    def inverse_kinematics(self, T_goal, q_init=None):
        """
        This method set end effector's position to T_des.

        :param np.ndarray[2D] T_goal: The target pose of robot's EE in SE(3)
        :param np.ndarray[1D] optional q_init: _description_, defaults to None
        :return List[float], radian: _description_
        """

        if q_init is None:
            q_init = self.q.reshape(self.numJoints)

        try:
            q_init.reshape(-1)
        except:
            pass

        return self.pinModel.CLIK(T_goal=T_goal, ql=self._jointpos_lower, qu=self._jointpos_upper, q_init=q_init)

    # control utils
    def reset_joint_pos(self, q_des):
        q_des = np.asarray(q_des).reshape(-1, 1)
        self._q_des = q_des

    def move_joint_pos(self, q_des):
        # Implement this function!
        pass

    def move_task_pos(self, T_des):
        # Implement this function!
        pass