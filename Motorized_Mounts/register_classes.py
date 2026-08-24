#####################################################################
#                                                                   #
# user_devices/Motorized_Mounts/register_classes.py                 #
#                                                                   #
# Registers the motorized mirror mounts with BLACS / runviewer.     #
#                                                                   #
#####################################################################
from labscript_devices import register_classes

register_classes(
    'MotorizedMirrorMounts',
    BLACS_tab='user_devices.Motorized_Mounts.blacs_tabs.MotorizedMirrorMountsTab',
    runviewer_parser=None,
)
