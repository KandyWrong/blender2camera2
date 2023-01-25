#  ____ ____   ____ ____          _   _____
# | __ )___ \ / ___|___ \  __   _/ | |___ /
# |  _ \ __) | |     __) | \ \ / / |   |_ \
# | |_) / __/| |___ / __/   \ V /| |_ ___) |
# |____/_____|\____|_____|   \_/ |_(_)____/
#
# Blender2Camera2 v1.3
# A Blender to Camera2 / Beat Saber Export Script
#
# Written by KandyWrong
#
# This script was released under the MIT license. See LICENSE.md for details.

import bpy
import copy
import hashlib
import json
import logging
import math
import mathutils
import os
import random
import time

from datetime import datetime

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty
from bpy.types import Operator

'''
Config Stuff
'''

# The script will search for cameras with this prefix in their name. You don't
# need to change this to use the script normally.
CONFIG_CAMERA_PREFIX = 'b2c2_'

# If you are working on a project that requires post-processing with chroma key
# effects (green screens), -AND- you are rendering something out of Blender to
# use in your Beat Saber video, enable this option to automatically set the
# Blender camera sensor values so that the Blender render will match the Beat
# Saber capture exactly.

# If you aren't doing any post-process special effects, or you aren't sure what
# this is, then leave the option disabled.

# Of course if you do know what this is and why it's needed, you probably
# have the necessary knowledge to change the Blender camera options on your own
# without B2C2.
CONFIG_CAMERA_SENSOR_FIX_FOV_FOR_BLENDER_RENDERS = False

CONFIG_CAMERA_SENSOR_FIT    = 'VERTICAL'
CONFIG_CAMERA_SENSOR_HEIGHT = 24.0
CONFIG_CAMERA_SENSOR_WIDTH  = 42.666666

# Set to true if you want the Blender system console output to be logged to a
# file on the disk. The log file will be written to the same directory as your
# blend file. This is only useful for debugging.
CONFIG_ENABLE_LOGGING_TO_DISK = True

# The script uses a temporary object to store matrix transforms during
# coordinate system conversion. This object is created when the script starts
# running and is removed when the script is done (if it doesn't crash, of
# course).
CONFIG_EXPORT_OBJECT_PREFIX = 'b2c2_export_object_'

'''
Logging Stuff
'''

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())

'''
> Translating from Blender -> Unity / Beat Saber

TL,DR:
    # Subtract 90 degrees on the Blender X axis, then
    (px, pz, py) = mw_unity.to_translation()
    (rx, rz, ry) = mw_unity.to_euler('YXZ')
    (rx, rz, ry) = (-rx, -rz, -ry)

    # ... and then you have Unity compatible coords + rotations.

Logic:
- Blender uses a Z-up, right-hand coordinate system
- Blender positive rotations are counter-clockwise

- Unity uses a Y-up, left-hand coordinate system
- Unity positive rotations are clockwise

For anyone who is reading this script and wants to know how I converted Blender
coordinates to Unity coordinates: the key is setting the right Euler rotation
order when exporting from Blender.

The Unity engine expects a Euler order of ZXY. See:

    https://docs.unity3d.com/ScriptReference/Transform-eulerAngles.html

In Camera2 terms, Unity sets the camera rotation in this order:
* rot Z = camera roll angle. Looking "down the barrel", where the camera lens
    is facing away from you, a positive Z rotation (roll) of 45 degrees would
    cause the camera to roll to the left (counter-clockwise).

* rot X = camera pitch angle. If you were to look at a camera such that the top
    of the camera was "up" and the lens of the camera was facing left, a
    positive X rotation (pitch) of 45 degrees would cause the camera to point
    downward (counter-clockwise).

* rot Y = camera yaw angle. Imagine looking at the Beat Saber platform from the
    top down, with the highway at the top / North / forward position. A
    positive Y rotation (yaw) of 45 degrees would cause the camera to point
    to the right (clockwise).

To export from Blender to Unity with minimal drama, you need to get the three
Euler angles from Blender in the right order. But don't think in terms of
X/Y/Z. Instead, think in terms of roll / pitch / yaw.

Before doing that, there's something else that has to be considered.

> Camera Orientation in Blender vs Unity

Blender cameras and Unity cameras interpret their pitch angles differently.

In Blender, a pitch angle of 0 degrees causes a camera to point straight down.

In Unity, a pitch angle of 0 degrees causes a camera to point straight forward,
toward the horizon. (Straight forward in Blender is +90 degrees of pitch.)

Thus, to translate from Blender -> Unity and end up with the same pitch
orientation, the B2C2 export script must subtract 90 degrees.

> Putting it All Together

Looking at this code:
    (rx, rz, ry) = mw_unity.to_euler('YXZ')

The Euler order is "YXZ" because the Unity engine expects ZXY -AND- because
the Y and Z axes are swapped between Blender and Unity. Thus to_euler() must
be called as though the Blender Z axis is actually Y, and vice versa.

The same logic applies to the output tuple. Z and Y are swapped.

Finally, the raw rotation angles in the tuple must be multiplied by -1 to
account for counter-clockwise vs clockwise sign difference between Blender and
Unity.

That's how the logic worked in my brain, and after -a lot- of testing by
comparing captured video, the script output seems to be dead on in Unity / Beat
Saber. If someone else has a better way to explain then leave an issue in the
github.
'''

'''
Classes
'''

class B2C2Export(Operator, ExportHelper):
    """Export camera path data to Beat Saber Camera2 format"""
    bl_idname = "b2c2_export.export"
    bl_label = "Export"

    # ExportHelper mixin class uses this
    filename_ext = ".json"

    filter_glob: StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    # List of operator properties, the attributes will be assigned
    # to the class instance from the operator settings before calling.
    setting_fixFovForBlenderRender: BoolProperty(
        name="Fix camera FOV for Blender renders",
        description="For projects that use post-process chroma key "        \
            "compositing (green screen), check this option to fix your "    \
            "camera FOV so that the FOV in your Blender renders will match "\
            "the FOV in Beat Saber",
        default=False,
    )

    setting_loop: BoolProperty(
        name="Loop Script",
        description="When checked, the movement script will loop if the "   \
            "song is longer than the script. Otherwise, the movement "      \
            "script will only play once and stop on the last keyframe",
        default=True,
    )

    setting_syncToSong: BoolProperty(
        name="Sync to Song",
        description="When checked, the movement script pauses when you "    \
            "pause the song",
        default=True,
    )

    def execute(self, context):

        # Add file log handler
        if (True == CONFIG_ENABLE_LOGGING_TO_DISK):
            logger_start_disk()

        # Export the movement script
        export_main(
                context,
                self.filepath,
                self.setting_fixFovForBlenderRender,
                self.setting_loop,
                self.setting_syncToSong)

        # Clean up log handlers
        handlers = logger.handlers[:]
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()

        return {'FINISHED'}


'''
Menu Stuff
'''

# Only needed if you want to add into a dynamic menu
def menu_func_export(self, context):
    self.layout.operator(B2C2Export.bl_idname, text="Beat Saber Camera2 Movement Script (.json)")


# Register and add to the "file selector" menu (required to use F3 search "Text Export Operator" for quick access)
def register():
    bpy.utils.register_class(B2C2Export)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.utils.unregister_class(B2C2Export)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)


'''
Export Functions
'''

def export_main(
        context,
        filepath,
        setting_fixFovForBlenderRender,
        setting_loop,
        setting_syncToSong):

    # Track script running time
    now_head = datetime.now()
    logger.debug('Export started at ' + str(now_head))

    '''
    Store a list of selected objects (if any)

    I find it annoying when a script runs and deselects everything, so B2C2
    will put things back the way it found them (if it doesn't crash).
    '''

    pre_selected_active_object = bpy.context.view_layer.objects.active.name

    pre_selected_objects = []
    for obj in bpy.context.selected_objects:
        pre_selected_objects.append(obj.name)

    # --------------------------------------------------------------------------

    '''
    Find Cameras

    Iterate through every object that is presently loaded from the blend file.
    Figure out which objects are cameras, and then pick out the cameras with
    the special prefix in their name.
    '''

    cameras = []

    for obj_name in bpy.data.objects.keys():
        if 'CAMERA' == bpy.data.objects[obj_name].type:
            camera_name = obj_name.lower()

            if camera_name.startswith(CONFIG_CAMERA_PREFIX):
                logger.debug('Found camera with name ' + obj_name)
                cameras.append(bpy.data.objects[obj_name])

    logger.debug('Found ' + str(len(cameras)) + ' camera(s) to export')

    # --------------------------------------------------------------------------

    '''
    Create Temporary Export Object

    hashlib is involved so B2C2 can generate a random name for the temporary
    object. If the export object name conflicts with something that is already
    in your Blend file, go buy a lottery ticket.
    '''
    m = hashlib.sha1()
    m.update(str(random.getrandbits(128)).encode('utf-8'))
    export_obj_name = CONFIG_EXPORT_OBJECT_PREFIX + str(m.hexdigest()[:20])

    bpy.ops.object.empty_add()
    export_obj = bpy.context.active_object
    export_obj.name = export_obj_name

    # --------------------------------------------------------------------------

    '''
    Collect Location, Rotation, & FOV

    For every frame in the scene, collect data from all the cameras that were
    found in the block above. All of these frames put together create the
    camera path, hence the name of the dictionary.

    This "data" is:
    - The camera position (in Blender X,Y,Z)
    - The camera rotation (in Blender X,Y,Z)
    - The camera field of view (in degrees)
    '''

    scene = context.scene
    paths = {}

    layer = bpy.context.view_layer

    # Loop through each (b2c2) camera in the scene.
    for camera in cameras:

        logger.debug('Retrieving data for camera    : ' + str(camera.name))

        # Fix the camera sensor values, so FOV in Blender renders will match
        # Beat Saber FOV (only needed for post-processing with chroma key
        # effects)
        if CONFIG_CAMERA_SENSOR_FIX_FOV_FOR_BLENDER_RENDERS or setting_fixFovForBlenderRender:
            camera.data.sensor_fit      = CONFIG_CAMERA_SENSOR_FIT
            camera.data.sensor_width    = CONFIG_CAMERA_SENSOR_WIDTH
            camera.data.sensor_height   = CONFIG_CAMERA_SENSOR_HEIGHT

        # For each frame in the scene...
        for frame in range(scene.frame_start, scene.frame_end + 1):

            # Set the scene to this frame.
            scene.frame_set(frame)
            layer.update()

            # Get the world position matrix of the current camera.
            mw_blender = camera.matrix_world

            # See comments under "Camera Orientation in Blender vs Unity".
            export_obj.matrix_world = mw_blender
            scene.frame_set(frame)
            layer.update()

            export_obj.rotation_euler[0] += math.radians(-90)
            scene.frame_set(frame)
            layer.update()

            mw_unity = copy.deepcopy(export_obj.matrix_world)

            '''
            Field-of-View (FOV) Notes

            The FOV value written in the Camera2 movement script is interpreted
            as the vertical FOV in the game. B2C2 has no control over this, it
            is just how Beat Saber / the Unity game engine works.

            In the context of Blender: how the FOV is applied depends on the
            aspect ratio of the final rendered image.

                * For landscape images the FOV applies to the horizontal (width)
                * For portrait images the FOV applies to the vertical (height)

            How Unity handles FOV:
            https://docs.unity3d.com/ScriptReference/Camera-fieldOfView.html

            How Blender handles FOV:
            https://blender.stackexchange.com/questions/23431/how-to-set-camera-horizontal-and-vertical-fov
            https://docs.blender.org/api/current/bpy.types.Camera.html

            Fortunately we don't have to worry about any of this mess, since
            Blender automatically provides the vertical FOV value with the
            camera data object.
            '''

            # Grab (vertical) field-of-view angle
            fov = math.degrees(camera.data.angle_y)

            '''
            Final Camera Output
            '''

            # Decompose Unity camera's position. See notes about to_euler()
            # under "Camera Orientation in Blender vs Unity".
            (px, pz, py) = mw_unity.to_translation()
            (rx, rz, ry) = mw_unity.to_euler('YXZ')

            # Pack Unity camera data in a temporary dictionary.
            dict_unity = {}
            dict_unity['frame'] = frame
            dict_unity['pos'] = (px, py, pz)
            dict_unity['rot'] = (-rx, -ry, -rz)
            dict_unity['fov'] = fov

            # Append data to the camera's list inside the path dictionary.
            if camera.name not in paths:
                paths[camera.name] = []

            paths[camera.name].append(dict_unity)

    # Clean up the temporary export object.
    export_obj.select_set(True)
    bpy.ops.object.delete()

    # --------------------------------------------------------------------------

    '''
    Re-select all previously selected objects
    '''

    # Deselect all selected objects
    for obj in bpy.context.selected_objects:
        obj.select_set(False)

    # Restore original selection
    for obj_name in pre_selected_objects:
        bpy.data.objects[obj_name].select_set(True)

    bpy.context.view_layer.objects.active = bpy.data.objects[pre_selected_active_object]

    # --------------------------------------------------------------------------

    '''
    Convert to exportable format

    The Camera2 movement scripts are stored in JSON format. The individual
    camera keyframes are stored within a single list titled "frames". Each
    frame is a dictionary that contains "position", "FOV", "rotation", and a
    few optional fields.

    The dictionary of paths created above needs to be converted into a new
    list / dict based structure so that exporting to the JSON file will be
    easier in the next step.
    '''

    movement = {}

    # Calculate frame duration (in seconds)
    duration = 1 / scene.render.fps

    for camera_name in paths:

        # Create the camera's frame list inside the path dictionary
        if camera_name not in movement:
            movement[camera_name] = {}

        # Store optional global settings
        movement[camera_name]['syncToSong'] = setting_syncToSong
        movement[camera_name]['loop']       = setting_loop

        # Store frames
        movement[camera_name]['frames'] = []

        for i,frame in enumerate(paths[camera_name]):
            temp = {}

            # Write the frame index to help with debugging. Camera2 ignores
            # this field with no ill effects.
            temp['frame_index'] = i + 1

            temp['position'] = {}
            temp['position']['x'] = round(frame['pos'][0], 3)
            temp['position']['y'] = round(frame['pos'][1], 3)
            temp['position']['z'] = round(frame['pos'][2], 3)

            temp['rotation'] = {}
            temp['rotation']['x'] = round(math.degrees(frame['rot'][0]), 3)
            temp['rotation']['y'] = round(math.degrees(frame['rot'][1]), 3)
            temp['rotation']['z'] = round(math.degrees(frame['rot'][2]), 3)

            temp['FOV'] = round(frame['fov'], 3)
            temp['duration'] = duration

            movement[camera_name]['frames'].append(temp)

    # --------------------------------------------------------------------------

    '''
    Write files to disk

    Now it's time to write each camera script to the disk. The export script
    creates a separate movement script for each camera it found in the blend
    file.
    '''

    path_base_noext = os.path.splitext(filepath)

    for camera_name in movement:
        path_target = path_base_noext[0] + '_' + camera_name + '.json'

        # Open file for writing
        with open(path_target, 'w') as fh:
            fh.write(json.dumps(movement[camera_name], indent=4, sort_keys=True))

    # Track script running time
    now_tail = datetime.now()

    logger.debug('Export finished at ' + str(now_tail))
    logger.debug('Export took ' + str(now_tail - now_head))

    return {'FINISHED'}


def logger_start_disk():

    '''
    Configure the local disk logger.
    '''
    try:

        # Create formatter
        formatter = logging.Formatter(
            fmt='[%(process)d] %(levelname)s: %(module)s.%(funcName)s(): %(message)s')

        # If the log directory does not exist, create it
        log_path = os.path.join(os.path.dirname(bpy.data.filepath), 'logs')
        if not os.path.exists(log_path):
            os.mkdir(log_path)

        # Build file path
        fh_path = os.path.basename(bpy.data.filepath)
        fh_path += '-' + time.strftime("%Y%m%d-%H%M%S") + '.log'
        fh_path = os.path.join(log_path, fh_path)

        # Point the logger to the file
        fh = logging.FileHandler(fh_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        return True

    except:
        raise
        return False


if __name__ == "__main__":
    register()

    bpy.ops.b2c2_export.export('INVOKE_DEFAULT')
