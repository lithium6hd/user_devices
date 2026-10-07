"""Minimal connection table for testing AravisCamera.

Contains nothing but dummy devices and the camera, because the camera is the
only hardware connected to this machine -- the production HQA connection table
cannot be loaded here.

To use, point connection_table_py in
labconfig/<hostname>.ini at this file and recompile.

Set SERIAL to 'Fake_1' to use Aravis' built-in fake camera, or set MOCK to
True to run with no Aravis at all.
"""
from labscript import start, stop
from labscript_devices.DummyPseudoclock.labscript_devices import DummyPseudoclock
from labscript_devices.DummyIntermediateDevice import DummyIntermediateDevice

from user_devices.AravisCamera.labscript_devices import AravisCamera

MOCK = False
SERIAL = 'Fake_1'   # or the real serial from arv-tool-0.8

DummyPseudoclock(name='dummy_clock', BLACS_connection='dummy')
DummyIntermediateDevice(
    name='dummy_device',
    parent_device=dummy_clock.clockline,
    BLACS_connection='dummy2',
)

AravisCamera(
    'test_cam',
    parent_device=dummy_device,
    connection='camera_trigger',
    serial_number=SERIAL,
    orientation='test',
    trigger_duration=1e-3,
    exception_on_failed_shot=False,
    mock=MOCK,
    camera_attributes={
        # Free-run: no trigger wire is connected to this camera.
        'TriggerMode':  'Off',
        'ExposureAuto': 'Off',
        'ExposureTime': 10000.0,
        'PixelFormat':  'Mono8',
    },
    manual_mode_camera_attributes={
        'TriggerMode': 'Off',
    },
)

if __name__ == '__main__':
    start()
    test_cam.expose(1.0, 'first', trigger_duration=1e-3)
    test_cam.expose(1.5, 'second', trigger_duration=1e-3)
    stop(2.0)
