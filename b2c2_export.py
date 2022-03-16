#  _    ____      ____          _   _
# | |__|___ \ ___|___ \  __   _/ | / |
# | '_ \ __) / __| __) | \ \ / / | | |
# | |_) / __/ (__ / __/   \ V /| |_| |
# |_.__/_____\___|_____|   \_/ |_(_)_|
#
# Blender2Camera2 v1.1
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
Export Functions
'''

# https://blender.stackexchange.com/questions/58916/script-for-save-camera-position-to-file
def export_main(context, filepath, setting_loop, setting_syncToSong):

    '''
    Find Cameras

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

    '''
    Collect Location, Rotation, & FOV

    For every frame in the scene, collect data from all the cameras that were
    found in the block above. All of these frames put together create the
    camera path, hence the name of the dictionary.

    This "data" is:
    * The camera position (in Blender X,Y,Z)
    * The camera rotation (in Blender X,Y,Z)
    * The camera field of view (in degrees)

    Info on rotation matrices can be found here:
    https://medium.com/macoclock/augmented-reality-911-transform-matrix-4x4-af91a9718246
    https://www.brainvoyager.com/bv/doc/UsersGuide/CoordsAndTransforms/SpatialTransformationMatrices.html

    Oddly enough, StackOverflow disagrees with using the matrix equations shown
    in the links above. Through a lot of experimentation I eventually settled
    on the matrix shown below. I am not a math expert so I don't know if it is
    truly correct. But it looks fine in the game, so I'm gonna use it for now.
    '''

    # Define the rotation matrix.
    mw_rotate_x = mathutils.Matrix()
    mw_rotate_x[0] = 1,    0,      0,      0
    mw_rotate_x[1] = 0,    0,      1,      0
    mw_rotate_x[2] = 0,    1,      0,      0
    mw_rotate_x[3] = 0,    0,      0,      1

    scene = context.scene
    frame = scene.frame_start

    paths = {}

    # For each frame in the scene...
    for frame in range(scene.frame_start, scene.frame_end + 1):

        # Set the scene to this frame.
        scene.frame_set(frame)

        # For each camera...
        for camera in cameras:

            '''
            Translating from Blender -> Unity / Beat Saber

            * Blender uses a Z-up, right-hand coordinate system
            * Unity uses a Y-up, left-hand coordinate system

            B2C2 converts between these coordinate systems by using a custom
            4x4 rotation matrix (that I determined by a lot of trial and error)
            and some post-processing to swap the axes into something that Unity
            expects.
            '''

            # Get the world position matrix of the current camera.
            mw = camera.matrix_world

            print('-' * 10)
            print('camera matrix_world')
            print(mw)

            '''
            Use "@" to multiply the camera matrix with the rotation matrix.
            The order of matrix multiplication here may not be correct. But
            like I said up above, it all looks fine in the game, so I'm gonna
            use it for now. Deeper investigation can wait until v1.2.
            '''
            mw_result = mw @ mw_rotate_x
            print('mw_result')
            print(mw_result)

            # Convert the camera position matrix to Euler rotation values.
            rot_out = mw_result.to_euler()

            # Flip rotation values so the camera points the right way in Unity.
            rx = rot_out[0]
            ry = rot_out[2] * -1
            rz = rot_out[1] * -1

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

            print('FOV H:   ' + str(math.degrees(camera.data.angle_x)))
            print('FOV V:   ' + str(math.degrees(camera.data.angle_y)))

            '''
            Final Camera Output
            '''

            # Grab world X,Y,Z position
            (x,y,z) = mw.to_translation()

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

    # Calculate frame duration
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
            temp['duration'] = duration

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
