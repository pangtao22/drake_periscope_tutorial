# -*- coding: utf8 -*-

import os.path
from matplotlib import cm
import numpy as np

import pydrake
from pydrake.all import (
    AddFlatTerrainToWorld,
    AddModelInstancesFromSdfString,
    AddModelInstanceFromUrdfFile,
    AddModelInstanceFromUrdfStringSearchingInRosPackages,
    FloatingBaseType,
    LeafSystem,
    PortDataType,
    RigidBodyFrame,
    RollPitchYaw,
    RotationMatrix,
    MathematicalProgram
)

import meshcat
import meshcat.transformations as tf
import meshcat.geometry as g

def GetCollisionElementToRigidBodyIndexMap(tree):
    map = {}
    n_bodies = tree.get_num_bodies()
    for i in range(n_bodies):
        for element in tree.get_body(i).get_collision_element_ids():
            map[element] = i

    return map

def PrintRbtInfo(tree):
    n_positions = tree.get_num_positions()
    for i in range(n_positions):
        print i, tree.get_position_name(i)

    print "-----------------"

    n_bodies = tree.get_num_bodies()
    for i in range(n_bodies):
        print i, tree.getBodyOrFrameName(i)

    print "Number of actuators:", tree.get_num_actuators()

class ContactLogger(LeafSystem):
    ''' Logs contact force history, using
        the rigid body plant contact result
        output port.

        Stores sample times, accessible via
        sample_times(), and contact results for
        each sample time, accessible as a list
        from method data().

        Every contact result is a list of tuples,
        one tuple for each contact,
        where each tuple contains (id_1, id_2, r, f, tau):
            id_1 = the ID of element #1 in collision
            id_2 = the ID of element #2 in collision
            r = the contact location, in world frame
            f = the contact force, in world frame
            tau = generalized contact force returned by
                contact_results.get_generalized_contact_force()'''

    def __init__(self, plant):
        LeafSystem.__init__(self)

        self._data = []
        self._num_contacts = []
        self._sample_times = np.empty((0, 1))
        self.shut_up = False
        # Contact results
        self._DeclareInputPort(PortDataType.kAbstractValued,
                               plant.contact_results_output_port().size())

    def data(self):
        return self._data

    def sample_times(self):
        return self._sample_times

    def _DoPublish(self, context, events):
        contact_results = self.EvalAbstractInput(context, 0).get_value()
        self._sample_times = np.vstack([self._sample_times, [context.get_time()]])
        self._num_contacts = np.hstack((self._num_contacts, \
                                        contact_results.get_num_contacts()))

        this_contact_info = []
        for contact_i in range(contact_results.get_num_contacts()):
            # if contact_i >= self.n_cf:
            #     if not self.shut_up:
            #         print "More contacts than expected (the # of grasp points). " \
            #               "Dropping some! Your fingertips probably touched each other."
            #         self.shut_up = True
            #     break
            # Cludgy -- would rather keep things as objects.
            # But I need to work out how to deepcopy those objects.
            # (Need to bind their various constructive methods)
            contact_info = contact_results.get_contact_info(contact_i)
            contact_force = contact_info.get_resultant_force()
            this_contact_info.append([
                contact_info.get_element_id_1(),
                contact_info.get_element_id_2(),
                contact_force.get_application_point(),
                contact_force.get_force(),
                contact_results.get_generalized_contact_force()
            ])
        self._data.append(this_contact_info)


def extract_position_indices(rbt, controlled_joint_names):
    ''' Given a RigidBodyTree and a list of
    joint names, returns, in separate lists, the
    position indices (i.e. offsets into the RBT positions vector)
    corresponding to those joints, and the rest of the
    position indices. '''
    controlled_config_inds = []
    other_config_inds = []
    for i in range(rbt.get_num_bodies()):
        body = rbt.get_body(i)
        if body.has_joint():
            joint = body.getJoint()
            if joint.get_name() in controlled_joint_names:
                controlled_config_inds += range(
                    body.get_position_start_index(),
                    body.get_position_start_index() +
                    joint.get_num_positions())
            else:
                other_config_inds += range(
                    body.get_position_start_index(),
                    body.get_position_start_index() +
                    joint.get_num_positions())
    if len(controlled_joint_names) != len(controlled_config_inds):
        raise ValueError("Didn't find all "
                         "requested controlled joint names.")

    return controlled_config_inds, other_config_inds


def setup_kuka(rbt):
    iiwa_urdf_path = os.path.join(
        pydrake.getDrakePath(),
        "manipulation", "models", "iiwa_description", "urdf",
        "iiwa14_polytope_collision.urdf")

    wsg50_sdf_path = os.path.join(
        pydrake.getDrakePath(),
        "manipulation", "models", "wsg_50_description", "sdf",
        "schunk_wsg_50.sdf")

    table_sdf_path = os.path.join(
        pydrake.getDrakePath(),
        "examples", "kuka_iiwa_arm", "models", "table",
        "extra_heavy_duty_table_surface_only_collision.sdf")

    object_urdf_path = os.path.join(
        pydrake.getDrakePath(),
        "examples", "kuka_iiwa_arm", "models", "objects",
        "block_for_pick_and_place.urdf")

    AddFlatTerrainToWorld(rbt)
    table_frame_robot = RigidBodyFrame(
        "table_frame_robot", rbt.world(),
        [0.0, 0, 0], [0, 0, 0])
    AddModelInstancesFromSdfString(
        open(table_sdf_path).read(), FloatingBaseType.kFixed,
        table_frame_robot, rbt)
    table_frame_fwd = RigidBodyFrame(
        "table_frame_fwd", rbt.world(),
        [0.8, 0, 0], [0, 0, 0])
    AddModelInstancesFromSdfString(
        open(table_sdf_path).read(), FloatingBaseType.kFixed,
        table_frame_fwd, rbt)

    table_top_z_in_world = 0.736 + 0.057 / 2

    robot_base_frame = RigidBodyFrame(
        "robot_base_frame", rbt.world(),
        [0.0, 0, table_top_z_in_world], [0, 0, 0])
    AddModelInstanceFromUrdfFile(iiwa_urdf_path, FloatingBaseType.kFixed,
                                 robot_base_frame, rbt)

    object_init_frame = RigidBodyFrame(
        "object_init_frame", rbt.world(),
        [0.8, 0.15, table_top_z_in_world+0.1], [0, 0, 0])
    AddModelInstanceFromUrdfFile(object_urdf_path,
                                 FloatingBaseType.kRollPitchYaw,
                                 object_init_frame, rbt)

    # Add gripper
    gripper_frame = rbt.findFrame("iiwa_frame_ee")
    AddModelInstancesFromSdfString(
        open(wsg50_sdf_path).read(), FloatingBaseType.kFixed,
        gripper_frame, rbt)


def render_system_with_graphviz(system, output_file="system_view.gz"):
    ''' Renders the Drake system (presumably a diagram,
    otherwise this graph will be fairly trivial) using
    graphviz to a specified file. '''
    from graphviz import Source
    string = system.GetGraphvizString()
    src = Source(string)
    src.render(output_file, view=False)


class RgbdCameraMeshcatVisualizer(LeafSystem):
    def __init__(self,
                 camera,
                 rbt,
                 draw_timestep=0.033333,
                 prefix="RBCameraViz",
                 zmq_url="tcp://127.0.0.1:6000"):
        LeafSystem.__init__(self)
        self.set_name('camera meshcat visualization')
        self.timestep = draw_timestep
        self._DeclarePeriodicPublish(draw_timestep, 0.0)
        self.camera = camera
        self.rbt = rbt
        self.prefix = prefix

        self.camera_input_port = \
            self._DeclareInputPort(PortDataType.kAbstractValued,
                                   camera.depth_image_output_port().size())
        self.state_input_port = \
            self._DeclareInputPort(PortDataType.kVectorValued,
                                   rbt.get_num_positions() +
                                   rbt.get_num_velocities())

        # Set up meshcat
        self.vis = meshcat.Visualizer(zmq_url=zmq_url)
        self.vis[prefix].delete()

    def _DoPublish(self, context, event):
        u_data = self.EvalAbstractInput(context, 0).get_value()
        x = self.EvalVectorInput(context, 1).get_value()
        w, h, _ = u_data.data.shape
        depth_image = u_data.data[:, :, 0]

        # Convert depth image to point cloud, with +z being
        # camera "forward"
        Kinv = np.linalg.inv(
            self.camera.depth_camera_info().intrinsic_matrix())
        U, V = np.meshgrid(np.arange(h), np.arange(w))
        points_in_camera_frame = np.vstack([
            U.flatten(),
            V.flatten(),
            np.ones(w*h)])
        points_in_camera_frame = Kinv.dot(points_in_camera_frame) * \
            depth_image.flatten()

        # The depth camera has some offset from the camera's root frame,
        # so take than into account.
        pose_mat = self.camera.depth_camera_optical_pose().matrix()
        points_in_camera_frame = pose_mat[0:3, 0:3].dot(points_in_camera_frame)
        points_in_camera_frame += np.tile(pose_mat[0:3, 3], [w*h, 1]).T

        kinsol = self.rbt.doKinematics(x)
        points_in_world_frame = self.rbt.transformPoints(
            kinsol,
            points_in_camera_frame,
            self.camera.frame().get_frame_index(),
            0)

        # Color points according to their normalized height
        min_height = 0.0
        max_height = 2.0
        colors = cm.jet(
            (points_in_world_frame[2, :]-min_height)/(max_height-min_height)
            ).T[0:3, :]

        self.vis[self.prefix]["points"].set_object(
            g.PointCloud(position=points_in_world_frame,
                         color=colors,
                         size=0.005))
