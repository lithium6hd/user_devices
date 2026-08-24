#####################################################################
#                                                                   #
# /user_devices/DCAMCamera/blacs_tabs.py                            #
#                                                                   #
# Jan 2023, Marvin Holten                                           #
# 2025, Johannes Schabbauer                                         #
#                                                                   #
#                                                                   #
#####################################################################

from labscript_devices.IMAQdxCamera.blacs_tabs import IMAQdxCameraTab
import labscript_utils.h5_lock
import h5py

class DCAMCameraTab(IMAQdxCameraTab):
    """Thin sub-class of obj:`IMAQdxCameraTab`.
    
    This sub-class only defines :obj:`worker_class` to point to the correct
    :obj:`DCAMCameraWorker`."""
    
    # override worker class
    worker_class = 'user_devices.DCAMCamera.blacs_workers.DCAMCameraWorker'

    def initialise_workers(self):
        table = self.settings['connection_table']
        connection_table_properties = table.find_by_name(self.device_name).properties
        # The device properties can vary on a shot-by-shot basis, but at startup we will
        # initially set the values that are configured in the connection table, so they
        # can be used for manual mode acquisition:
        with h5py.File(table.filepath, 'r') as f:
            device_properties = labscript_utils.properties.get(
                f, self.device_name, "device_properties"
            )
        worker_initialisation_kwargs = {
            'serial_number': connection_table_properties['serial_number'],
            'orientation': connection_table_properties['orientation'],
            'camera_attributes': device_properties['camera_attributes'],
            'manual_mode_camera_attributes': connection_table_properties[
                'manual_mode_camera_attributes'
            ],
            'mock': connection_table_properties['mock'],
            'image_receiver_port': self.image_receiver.port,
            'occupation_receiver_port' : connection_table_properties.get('occupation_receiver_port',None)
        }
        self.create_worker(
            'main_worker', self.worker_class, worker_initialisation_kwargs
        )
        self.primary_worker = "main_worker"