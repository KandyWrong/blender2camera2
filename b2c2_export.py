#  _    ____      ____
# | |__|___ \ ___|___ \
# | '_ \ __) / __| __) |
# | |_) / __/ (__ / __/
# |_.__/_____\___|_____|
#
# A Blender to Camera2 / Beat Saber Export Script
#
# Written by KandyWrong
#
# This script was released under the MIT license. See LICENSE.md for details.

import bpy
import json
import math
import mathutils
import os
import time

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator

'''
Config Stuff
'''

# The script will search for cameras with this prefix in their name. You don't
# need to change this to use the script normally.
CONFIG_CAMERA_PREFIX = 'b2c2_'

'''
Classes
'''

class B2C2Export(Operator, ExportHelper):
    """Export camera path data to Beat Saber Camera2 format"""
    bl_idname = "export_test.some_data"  # important since its how bpy.ops.import_test.some_data is constructed
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
    setting_loop: BoolProperty(
        name="Loop Script",
        description="When false (default), the movement script will only play once and stay on the last keyframe.",
        default=False,
    )

    setting_syncToSong: BoolProperty(
        name="Sync to Song",
        description="When true, the movement script pauses when you pause the song and vice versa.",
        default=True,
    )

    def execute(self, context):

        return export_main(context, self.filepath, self.setting_loop, self.setting_syncToSong)


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
Helper Functions
'''

def calculate_fov(sensor_width, lens):

    '''
    https://b3d.interplanety.org/en/how-to-get-camera-fov-in-degrees-from-focal-length-in-mm/

    Given some sensor size and focal length, calculate the camera field of view
    (in degrees).
    '''

    radians = 2 * math.atan(sensor_width / (2 * lens))

    return math.degrees(radians)


def fix_rotation_offset(input):

    '''
    Fix Blender -> Unity rotation offset (AFTER quaternion drama is done).
    '''

    # Multiply by negative 1
    output = input * -1.0

    # If negative, add 360 degrees
    if (0 > output):
        output += math.radians(360)

    return output


'''
Export Functions
'''

# https://blender.stackexchange.com/questions/58916/script-for-save-camera-position-to-file
def export_main(context, filepath, setting_loop, setting_syncToSong):

    # --------------------------------------------------------------------------
    # Find Cameras

    '''
    Iterate through every object that is presently loaded from the blend-file.
    Figure out which objects are cameras, and then pick out the cameras with
    the special prefix in their name.
    '''

    cameras = []

    for objName in bpy.data.objects.keys():
        if 'CAMERA' == bpy.data.objects[objName].type:
            camera_name = objName.lower()

            if camera_name.startswith(CONFIG_CAMERA_PREFIX):
                print('found camera with name ' + objName)
                cameras.append(bpy.data.objects[objName])

    print('Found ' + str(len(cameras)) + ' camera(s) to export')

    # --------------------------------------------------------------------------
    # Collect Location, Rotation, & FOV

    '''
    For every frame in the scene, collect data from all the cameras that were
    found in the block above. All of these frames put together create the
    camera path, hence the name of the dictionary.

    This "data" is:
    * The camera position (in Blender X,Y,Z)
    * The camera rotation (in Blender X,Y,Z)
    * The camera field of view (in degrees)
    '''

    scene = context.scene
    frame = scene.frame_start

    paths = {}

    # For each frame in the scene...
    for frame in range(scene.frame_start, scene.frame_end + 1):

        # Set the scene to this frame.
        scene.frame_set(frame)

        # For each camera...
        for camera in cameras:

            # Get the world position matrix of the current camera.
            mw = camera.matrix_world

            # Grab world X,Y,Z position
            (x,y,z) = mw.to_translation()

            # Grab world X,Y,Z rotation
            (rx,ry,rz) = mw.to_euler('XYZ')

            # Convert Blender -> Unity rotation (absolute value only)
            rot_base  = mathutils.Euler((math.radians(-90.0), 0.0, 0.0), 'XYZ').to_quaternion()
            rot_out = (rot_base @ mw.to_quaternion()).to_euler()

            # Final rotation output
            rx = fix_rotation_offset(rot_out[0])
            ry = fix_rotation_offset(rot_out[1])
            rz = fix_rotation_offset(rot_out[2])

            # Grab field-of-view angle
            fov = calculate_fov(camera.data.sensor_width, camera.data.lens)

            # Pack camera data in a temporary dict
            temp = {}
            temp['frame'] = frame
            temp['pos'] = (x,z,y)       # Flip Blender Z/Y -> Beat Saber Y/Z
            temp['rot'] = (rx,ry,rz)
            temp['fov'] = fov

            # Append data to the camera's list inside the data dictionary
            if camera.name not in paths:
                paths[camera.name] = []

            paths[camera.name].append(temp)

    # --------------------------------------------------------------------------
    # Convert to exportable format

    '''
    The Camera2 movement scripts are stored in JSON format. The individual
    camera keyframes are stored within a single list titled "frames". Each
    frame is a dictionary that contains "position", "FOV", "rotation", and a
    few optional fields.

    The dictionary of paths created above needs to be converted into a new
    list / dict based structure so exporting to the JSON file will be easier
    in the next step.
    '''

    movement = {}

    # Calculate hold time
    holdTime = 1 / scene.render.fps

    for camera_name in paths:

        # Create the camera's frame list inside the path dictionary
        if camera_name not in movement:
            movement[camera_name] = {}

        # Store optional global settings
        movement[camera_name]['syncToSong'] = setting_syncToSong
        movement[camera_name]['loop']       = setting_loop

        # Store frames
        movement[camera_name]['frames'] = []

        for frame in paths[camera_name]:
            temp = {}

            temp['position'] = {}
            temp['position']['x'] = round(frame['pos'][0], 3)
            temp['position']['y'] = round(frame['pos'][1], 3)
            temp['position']['z'] = round(frame['pos'][2], 3)

            temp['rotation'] = {}
            temp['rotation']['x'] = round(math.degrees(frame['rot'][0]), 3)
            temp['rotation']['y'] = round(math.degrees(frame['rot'][1]), 3)
            temp['rotation']['z'] = round(math.degrees(frame['rot'][2]), 3)

            temp['FOV'] = round(frame['fov'], 3)
            temp['holdTime'] = holdTime

            movement[camera_name]['frames'].append(temp)

    # --------------------------------------------------------------------------
    # Write files to disk

    '''
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

    print('done')
    return {'FINISHED'}


if __name__ == "__main__":
    register()

    # test call
    bpy.ops.export_test.some_data('INVOKE_DEFAULT')
