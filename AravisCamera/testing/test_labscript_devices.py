"""Tests for the AravisCamera labscript device class."""
import labscript_utils.h5_lock  # noqa: F401  (must precede h5py)

import h5py
import pytest

import labscript_utils.properties
from user_devices.AravisCamera.labscript_devices import AravisCamera


def _compile(h5_path, camera):
    """Run a minimal shot so device properties get written to the h5 file."""
    import labscript

    labscript.start()
    camera.expose(1.0, "img", trigger_duration=0.1)
    labscript.stop(2.0)


def test_serial_number_is_preserved_as_string(labscript_shot, dummy_parent):
    """Aravis serials are opaque strings and must not be parsed as hex.

    IMAQdxCamera does int(serial, 16), which would turn '15551991' into
    357898641 and silently address the wrong camera.
    """
    camera = AravisCamera(
        "test_cam",
        parent_device=dummy_parent,
        connection="do0",
        serial_number="15551991",
        camera_attributes={"TriggerMode": "Off"},
        manual_mode_camera_attributes={"TriggerMode": "Off"},
    )
    assert camera.serial_number == "15551991"
    assert camera.BLACS_connection == "15551991"


def test_serial_number_round_trips_through_h5(labscript_shot, dummy_parent):
    """The worker reads the serial from connection_table_properties, so the
    string must survive compilation, not just live on the instance."""
    camera = AravisCamera(
        "test_cam",
        parent_device=dummy_parent,
        connection="do0",
        serial_number="15551991",
        camera_attributes={"TriggerMode": "Off"},
        manual_mode_camera_attributes={"TriggerMode": "Off"},
    )
    _compile(labscript_shot, camera)

    with h5py.File(labscript_shot, "r") as f:
        props = labscript_utils.properties.get(
            f, "test_cam", "connection_table_properties"
        )
    assert props["serial_number"] == "15551991"


def test_non_numeric_serial_is_accepted(labscript_shot, dummy_parent):
    """Aravis serials need not be numeric; hex parsing would reject these."""
    camera = AravisCamera(
        "test_cam",
        parent_device=dummy_parent,
        connection="do0",
        serial_number="Fake_1",
        camera_attributes={"TriggerMode": "Off"},
        manual_mode_camera_attributes={"TriggerMode": "Off"},
    )
    assert camera.serial_number == "Fake_1"


def test_manual_mode_attribute_not_in_camera_attributes_is_rejected(
    labscript_shot, dummy_parent
):
    """Inherited validation: manual-mode keys must also appear in
    camera_attributes, or they would never be restored after a shot."""
    with pytest.raises(ValueError, match="ExposureTime"):
        AravisCamera(
            "test_cam",
            parent_device=dummy_parent,
            connection="do0",
            serial_number="15551991",
            camera_attributes={"TriggerMode": "Off"},
            manual_mode_camera_attributes={"ExposureTime": 1000.0},
        )


def test_invalid_visibility_level_is_rejected(labscript_shot, dummy_parent):
    with pytest.raises(ValueError, match="saved_attribute_visibility_level"):
        AravisCamera(
            "test_cam",
            parent_device=dummy_parent,
            connection="do0",
            serial_number="15551991",
            saved_attribute_visibility_level="nonsense",
        )
