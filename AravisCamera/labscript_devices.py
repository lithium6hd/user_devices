#####################################################################
#                                                                   #
# /user_devices/AravisCamera/labscript_devices.py                   #
#                                                                   #
# Aravis (GenICam) camera support for labscript.                    #
#                                                                   #
#####################################################################
from labscript import TriggerableDevice, set_passed_properties
from labscript_utils import dedent

from labscript_devices.IMAQdxCamera.labscript_devices import IMAQdxCamera


class AravisCamera(IMAQdxCamera):
    """A GenICam camera driven through the Aravis library.

    Thin subclass of :obj:`IMAQdxCamera`. All shot sequencing, triggering and
    HDF5 output are inherited; only serial number handling differs.

    Camera attributes are bare GenICam feature names as reported by
    ``arv-tool-0.8`` -- e.g. ``'ExposureTime'``, not IMAQdx's
    ``'AcquisitionControl::ExposureTime'``.
    """

    description = 'Aravis GenICam Camera'

    @set_passed_properties(
        property_names={
            "connection_table_properties": [
                "serial_number",
                "orientation",
                "pixel_size",
                "magnification",
                "manual_mode_camera_attributes",
                "mock",
            ],
            "device_properties": [
                "camera_attributes",
                "stop_acquisition_timeout",
                "exception_on_failed_shot",
                "saved_attribute_visibility_level",
            ],
        }
    )
    def __init__(
        self,
        name,
        parent_device,
        connection,
        serial_number,
        orientation=None,
        pixel_size=[1.0, 1.0],
        magnification=1.0,
        trigger_edge_type='rising',
        trigger_duration=None,
        minimum_recovery_time=0.0,
        camera_attributes=None,
        manual_mode_camera_attributes=None,
        stop_acquisition_timeout=5.0,
        exception_on_failed_shot=True,
        saved_attribute_visibility_level='intermediate',
        mock=False,
        **kwargs
    ):
        """See :obj:`IMAQdxCamera` for the full argument documentation.

        The only behavioural difference is ``serial_number``: Aravis reports
        the GenICam ``DeviceSerialNumber``, an opaque string, not to be
        assumed decimal -- the bench camera reports ``0159787F``, which
        contains hex letters, so ``int(serial, 16)`` *succeeds* on it and
        silently returns 22640767 rather than raising. ``IMAQdxCamera``
        parses string serials as *hexadecimal*, which would silently address
        the wrong camera, so this class does not call
        ``IMAQdxCamera.__init__`` and instead reproduces its setup with the
        hex conversion removed.
        """
        self.trigger_edge_type = trigger_edge_type
        self.minimum_recovery_time = minimum_recovery_time
        self.trigger_duration = trigger_duration
        self.orientation = orientation
        self.pixel_size = pixel_size
        self.magnification = magnification

        # Deliberately NOT int(serial_number, 16): see the docstring above.
        self.serial_number = str(serial_number)
        self.BLACS_connection = self.serial_number

        if camera_attributes is None:
            camera_attributes = {}
        if manual_mode_camera_attributes is None:
            manual_mode_camera_attributes = {}
        for attr_name in manual_mode_camera_attributes:
            if attr_name not in camera_attributes:
                msg = f"""attribute '{attr_name}' is present in
                    manual_mode_camera_attributes but not in camera_attributes.
                    Attributes that are to differ between manual mode and buffered
                    mode must be present in both dictionaries."""
                raise ValueError(dedent(msg))

        valid_attr_levels = ('simple', 'intermediate', 'advanced', None)
        if saved_attribute_visibility_level not in valid_attr_levels:
            msg = "saved_attribute_visibility_level must be one of %s"
            raise ValueError(msg % (valid_attr_levels,))

        self.camera_attributes = camera_attributes
        self.manual_mode_camera_attributes = manual_mode_camera_attributes
        self.exposures = []

        TriggerableDevice.__init__(self, name, parent_device, connection, **kwargs)
