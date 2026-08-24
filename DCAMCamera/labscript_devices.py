#####################################################################
#                                                                   #
# /user_devices/DCAMCamera/labscript_devices.py                     #
#                                                                   #
# Jan 2023, Marvin Holten                                           #
# 2025, Johannes Schabbauer                                         #

#                                                                   #
#                                                                   #
#####################################################################

import h5py
from labscript import set_passed_properties, LabscriptError
from labscript_devices.IMAQdxCamera.labscript_devices import IMAQdxCamera

class DCAMCamera(IMAQdxCamera):
    """Thin sub-class of :obj:`IMAQdxCamera`."""
    
    description = 'DCAM Camera'

    @set_passed_properties(
        property_names={
            "connection_table_properties": [
                "occupation_receiver_port",
            ],
        }
    )
    def __init__(self, name, parent_device, connection, serial_number, orientation=None, pixel_size=..., magnification=1, trigger_edge_type='rising', trigger_duration=None, minimum_recovery_time=0, camera_attributes=None, manual_mode_camera_attributes=None, stop_acquisition_timeout=5, exception_on_failed_shot=True, saved_attribute_visibility_level='intermediate', occupation_receiver_port=None, mock=False, **kwargs):
        self.occupation_receiver_image_index = None
        self.exposure_times = []
        super().__init__(name, parent_device, connection, serial_number, orientation, pixel_size, magnification, trigger_edge_type, trigger_duration, minimum_recovery_time, camera_attributes, manual_mode_camera_attributes, stop_acquisition_timeout, exception_on_failed_shot, saved_attribute_visibility_level, mock, **kwargs)

    def expose(self, t, name, frametype='frame', trigger_duration=None, occupation_receiver=False):
        self.exposure_times.append(t)
        if occupation_receiver:
            if getattr(self,"occupation_receiver_time_"+occupation_receiver,None) is not None:
                raise LabscriptError(f"{self.name}: In the current implementation only a single image and bright pic can be sent to the 'occupation receiver'")
            if occupation_receiver not in ["atoms","bright"]:
                raise LabscriptError(f"'occupation_receiver' can only be 'atoms' or 'bright'.")
            setattr(self,"occupation_receiver_time_"+occupation_receiver, t)
        
        return super().expose(t, name, frametype, trigger_duration)

    def generate_code(self, hdf5_file):
        super().generate_code(hdf5_file)
        if getattr(self,"occupation_receiver_time_atoms",None) is not None:
            self.occupation_receiver_image_index = self.exposure_times.index(self.occupation_receiver_time_atoms)
            hdf5_file[f"devices/{self.name}"].attrs["occupation_receiver_image_index"] = self.occupation_receiver_image_index
            if getattr(self,"occupation_receiver_time_bright",None) is not None:
                self.occupation_receiver_bright_index = self.exposure_times.index(self.occupation_receiver_time_bright)
                hdf5_file[f"devices/{self.name}"].attrs["occupation_receiver_bright_index"] = self.occupation_receiver_bright_index
                # Error check: bright pic must be taken before atoms pic if we want to calculate occupoation matrix mid-shot
                if self.occupation_receiver_image_index < self.occupation_receiver_bright_index:
                    raise LabscriptError("When calculation the occupation mid-shot, the bright pic must be taken before the atom pic!")
            
