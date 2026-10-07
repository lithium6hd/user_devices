#####################################################################
#                                                                   #
# /user_devices/AravisCamera/blacs_tabs.py                          #
#                                                                   #
#####################################################################
from labscript_devices.IMAQdxCamera.blacs_tabs import IMAQdxCameraTab


class AravisCameraTab(IMAQdxCameraTab):
    """Thin subclass of :obj:`IMAQdxCameraTab`.

    Only overrides worker_class; the inherited initialise_workers passes
    exactly the arguments AravisCameraWorker needs.
    """

    worker_class = 'user_devices.AravisCamera.blacs_workers.AravisCameraWorker'
