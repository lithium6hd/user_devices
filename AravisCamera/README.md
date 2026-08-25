# AravisCamera

A labscript device for GenICam cameras driven through the
[Aravis](https://github.com/AravisProject/aravis) library. Tested with FLIR
USB3 Vision cameras (Chameleon3, Grasshopper3, Firefly, Blackfly), but Aravis
is vendor-neutral and the driver is not FLIR-specific.

Use this instead of `SpinnakerCamera` on Linux: FLIR's PySpin ships prebuilt
binaries pinned to specific Python and Ubuntu versions, and `IMAQdxCamera`
requires NI-Vision, which is Windows-only.

## Installation

### 1. Install Aravis

```bash
sudo apt install libaravis-0.8-0 gir1.2-aravis-0.8 aravis-tools-cli
```

### 2. Make PyGObject available to the labscript venv

PyGObject publishes no binary wheels, and building it from source needs a C
toolchain plus GLib and cairo development headers. The Aravis bindings
themselves come from the `gir1.2-aravis-0.8` apt package regardless — `gi` is
only a bridge that reads system-installed `.typelib` files — so there is no
pip-only path. Use the system PyGObject, which is built against the matching
GLib:

```bash
SP=/home/ultracold/labscript-suite/.labscript/lib/python3.14/site-packages
ln -s /usr/lib/python3/dist-packages/gi "$SP/gi"
ln -s /usr/lib/python3/dist-packages/PyGObject-3.56.2.dist-info \
      "$SP/PyGObject-3.56.2.dist-info"
```

This works because the venv uses the same CPython as the system. Adjust the
version numbers if either changes. To undo, delete the two symlinks.

Verify:

```bash
/home/ultracold/labscript-suite/.labscript/bin/python -c \
  "import gi; gi.require_version('Aravis','0.8'); from gi.repository import Aravis; print('OK')"
```

### 3. Grant access to the camera

Without this the camera enumerates but cannot be opened: the USB device node
is root-owned and read-only for normal users.

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1e10", MODE="0660", GROUP="plugdev"' \
  | sudo tee /etc/udev/rules.d/80-aravis-flir.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Vendor `1e10` is FLIR / Point Grey. Make sure your user is in `plugdev`
(`id -nG`). Replug the camera if the permissions do not change.

### 4. Raise the usbfs buffer limit

The kernel default of 16 MB is too small for USB3 Vision cameras. `usbcore` is
built into the kernel, so `modprobe.d` will not work:

```bash
echo 'w /sys/module/usbcore/parameters/usbfs_memory_mb - - - - 1000' \
  | sudo tee /etc/tmpfiles.d/usbfs-aravis.conf
sudo systemd-tmpfiles --create
```

Verify with `cat /sys/module/usbcore/parameters/usbfs_memory_mb` — expect `1000`.

### 5. Confirm

```bash
arv-tool-0.8
```

Should list your camera. Note the serial number: it is what you put in the
connection table, and it differs from an IMAQdx-format serial.

## Usage

```python
from user_devices.AravisCamera.labscript_devices import AravisCamera

AravisCamera(
    "MOT_Counting",
    parent_device=DIO32_1,
    connection="21",
    serial_number="15551991",
    orientation="PoC6",
    camera_attributes={
        'ExposureAuto':      'Off',
        'ExposureTime':      5000.0,
        'GainAuto':          'Off',
        'Gain':              0,
        'TriggerMode':       'On',
        'TriggerSource':     'Line0',
        'TriggerSelector':   'FrameStart',
        'TriggerActivation': 'RisingEdge',
        'AcquisitionMode':   'Continuous',
        'PixelFormat':       'Mono16',
    },
    manual_mode_camera_attributes={
        'TriggerMode': 'Off',
    },
)
```

Attribute names are **bare GenICam feature names** as reported by
`arv-tool-0.8 features` — not IMAQdx's `Category::Feature` form. So
`'ExposureTime'`, not `'AcquisitionControl::ExposureTime'`.

Keys in `manual_mode_camera_attributes` must also appear in
`camera_attributes`, otherwise labscript raises at compile time.

`TriggerMode='On'` in `camera_attributes` with `'Off'` in
`manual_mode_camera_attributes` gives hardware-triggered buffered shots and
free-running manual mode, switched automatically at each transition.

### Serial numbers

`serial_number` is an opaque string, taken verbatim from Aravis'
`DeviceSerialNumber`. Unlike `IMAQdxCamera`, it is **not** parsed as
hexadecimal. Migrating from `IMAQdxCamera` means re-reading the serial with
`arv-tool-0.8`; the IMAQdx-format value will not match.

### Supported pixel formats

`Mono8` and `Mono16` only. Packed formats such as `Mono12Packed` raise a clear
error rather than decoding incorrectly.

## Troubleshooting

Run the standalone smoke test first — it isolates Aravis from labscript and
BLACS:

```bash
.labscript/bin/python AravisCamera/testing/aravis_smoke_test.py
```

| Symptom | Cause |
|---|---|
| `No module named 'gi'` | Step 2 symlinks missing |
| `Namespace Aravis not available` | `gir1.2-aravis-0.8` not installed |
| Camera listed by `lsusb` but not `arv-tool-0.8` | udev rule not applied; replug the camera |
| Buffer allocation failures, incomplete or torn frames | `usbfs_memory_mb` still 16; see step 4 |
| Camera found but runs far below its rated framerate | Plugged into a USB2 port; check `lsusb -t` for 5000M |
| `No camera with serial ...` | Serial is in IMAQdx hex format; re-read with `arv-tool-0.8` |
| Acquisition times out during a shot | No trigger reaching the camera; check wiring and `TriggerSource` |
| `RuntimeError: Double import!` when running pytest | `AravisCamera/pytest.ini` missing or edited; it sets `--import-mode=importlib`, required so the package is never imported both as a bare top-level name and as `user_devices.AravisCamera...` — see the comment in that file |

## Testing

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/ -v
```

Unit tests need neither Aravis nor a camera. `testing/connection_table_aravis_test.py`
is a minimal all-dummy connection table for BLACS testing without any other
hardware connected.

`AravisCamera/pytest.ini` sets `--import-mode=importlib`. This is required,
not cosmetic: pytest's default "prepend" import mode would import
`AravisCamera/__init__.py` under a bare top-level name (`AravisCamera`) while
the tests import the same file as `user_devices.AravisCamera...`, and
labscript's double-import guard (`labscript_utils.double_import_denier`)
aborts every test with `RuntimeError: Double import!` when it sees the same
module loaded under two names. Do not delete `pytest.ini` as cruft.
