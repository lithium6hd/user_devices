"""Standalone Aravis check. Runs outside BLACS and outside labscript.

Use this first when the camera misbehaves: it isolates Aravis from the rest
of the stack.

Usage:
    .labscript/bin/python AravisCamera/testing/aravis_smoke_test.py
    .labscript/bin/python AravisCamera/testing/aravis_smoke_test.py <serial>
    .labscript/bin/python AravisCamera/testing/aravis_smoke_test.py Fake_1
"""
import sys

import gi

gi.require_version('Aravis', '0.8')
from gi.repository import Aravis  # noqa: E402


def list_devices():
    Aravis.update_device_list()
    n = Aravis.get_n_devices()
    print(f"Found {n} device(s):")
    for i in range(n):
        print(
            f"  [{i}] id={Aravis.get_device_id(i)!r} "
            f"serial={Aravis.get_device_serial_nbr(i)!r}"
        )
    return n


def open_camera(serial):
    if serial is None:
        Aravis.update_device_list()
        if Aravis.get_n_devices() == 0:
            raise SystemExit("No cameras found.")
        return Aravis.Camera.new(Aravis.get_device_id(0))
    if str(serial).startswith('Fake'):
        Aravis.enable_interface('Fake')
        return Aravis.Camera.new(str(serial))
    Aravis.update_device_list()
    for i in range(Aravis.get_n_devices()):
        if Aravis.get_device_serial_nbr(i) == str(serial):
            return Aravis.Camera.new(Aravis.get_device_id(i))
    raise SystemExit(f"No camera with serial {serial!r}.")


def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else None
    if serial is None or not str(serial).startswith('Fake'):
        list_devices()

    camera = open_camera(serial)
    print(f"\nOpened: {camera.get_model_name()} "
          f"(vendor {camera.get_vendor_name()})")

    x, y, width, height = camera.get_region()
    print(f"Region: {width}x{height} at ({x}, {y})")
    print(f"Pixel format: {camera.get_pixel_format_as_string()}")
    print(f"Payload: {camera.get_payload()} bytes")

    print("\nGrabbing one frame...")
    buffer = camera.acquisition(0)
    if buffer is None:
        raise SystemExit("Acquisition returned no buffer.")
    data = buffer.get_data()
    print(f"  status={buffer.get_status()} "
          f"size={len(data)} bytes "
          f"{buffer.get_image_width()}x{buffer.get_image_height()}")

    from user_devices.AravisCamera.aravis_utils import decode_buffer

    image = decode_buffer(
        data, buffer.get_image_width(), buffer.get_image_height()
    )
    print(f"  decoded: shape={image.shape} dtype={image.dtype} "
          f"min={image.min()} max={image.max()}")
    print("\nSmoke test OK.")


if __name__ == '__main__':
    main()
