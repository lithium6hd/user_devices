#####################################################################
#                                                                   #
# /user_devices/DCAMCamera/register_classes.py                      #
#                                                                   #
# Jan 2023, Marvin Holten                                           #
#                                                                   #
#                                                                   #
#####################################################################

from labscript_devices import register_classes

register_classes(
    'DCAMCamera',
    BLACS_tab='user_devices.DCAMCamera.blacs_tabs.DCAMCameraTab',
    runviewer_parser=None,
)
