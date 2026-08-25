#####################################################################
#                                                                   #
# /user_devices/AravisCamera/register_classes.py                    #
#                                                                   #
#####################################################################
from labscript_devices import register_classes

register_classes(
    'AravisCamera',
    BLACS_tab='user_devices.AravisCamera.blacs_tabs.AravisCameraTab',
    runviewer_parser=None,
)
