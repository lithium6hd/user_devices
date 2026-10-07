# AravisCamera

A labscript device for GenICam cameras driven through the
[Aravis](https://github.com/AravisProject/aravis) library. Validated against a
FLIR Blackfly S BFS-U3-16S2M (see "Validation status" below); the lab also
owns Chameleon3, Grasshopper3 and Firefly USB3 Vision cameras which have not
themselves been tested with this driver. Aravis itself is vendor-neutral and
the driver is not FLIR-specific.

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
`manual_mode_camera_attributes` is the intended configuration for
hardware-triggered buffered shots with free-running manual mode, switched
automatically at each transition — but this has not itself been exercised
against real trigger hardware; see "Validation status" below before relying
on it.

### Serial numbers

`serial_number` is an opaque string, taken verbatim from Aravis'
`DeviceSerialNumber`. Unlike `IMAQdxCamera`, it is **not** parsed as
hexadecimal. Migrating from `IMAQdxCamera` means re-reading the serial with
`arv-tool-0.8`; the IMAQdx-format value will not match.

### Fake camera (`Fake_1`)

A `serial_number` starting with `'Fake'` (e.g. `'Fake_1'`) selects Aravis'
built-in simulated GenICam device instead of a real camera — no hardware,
udev rule, or usbfs tweak required. It is addressed by that id rather than a
real serial, streams a synthetic test pattern, and is useful for exercising
the labscript/BLACS/worker plumbing (connection tables, BLACS tab startup,
manual-mode Snap, buffered shots) without a camera plugged in. See
`testing/connection_table_aravis_test.py`, whose default `SERIAL` is
`'Fake_1'` for exactly this reason, and `testing/aravis_smoke_test.py Fake_1`.

### Supported pixel formats

`Mono8` and `Mono16` only. Packed formats such as `Mono12Packed` raise a clear
error rather than decoding incorrectly.

### Attribute ordering

`apply_with_retry` (used for both `camera_attributes` and
`manual_mode_camera_attributes`) recovers from *range-style* interdependence
between features — e.g. setting `Width` before `OffsetX` fails because the
old offset plus the new width exceeds the sensor, then succeeds on a second
pass once `OffsetX` has also been applied — because the retry pass runs only
after every attribute has had a first attempt.

It cannot recover from *lock-style* interdependence, where setting feature A
locks feature B against being written at all: the retry fails identically,
since nothing changed between attempts. On the tested Blackfly S, the
trigger features are locked by `SequencerMode`/`ExposureMode` rather than by
`TriggerMode`, so the example dict above (which sets `TriggerMode` in the
middle) is safe as written on this camera. Other GenICam vendors do lock
trigger-related features on `TriggerMode` itself. As a defensive habit
regardless of vendor, put `TriggerMode` last in `camera_attributes`.

### Manual-mode Stop latency

Stopping continuous (manual-mode) acquisition can now take up to
`GRAB_TIMEOUT_S` (5 s by default) if the Stop button is pressed while `grab()`
is mid-poll for a frame. This is a deliberate trade-off: previously, the
continuous-acquisition thread would *die* outright on the first frame that
took longer than 200 ms to arrive, leaving a live Stop button in the GUI with
no further images ever arriving. `GRAB_TIMEOUT_S` is a class attribute on
`Aravis_Camera`, so a subclass can override it to shorten (or lengthen) that
ceiling. An exposure longer than `GRAB_TIMEOUT_S` in continuous mode produces
a clean `TimeoutError` rather than a wedged worker.

## Validation status

Validated programmatically on Ubuntu 26.04, Python 3.14.4, Aravis 0.8.34,
against a FLIR Blackfly S BFS-U3-16S2M (serial `0159787F`, 1440x1080 Mono8,
USB3):

- Enumeration by serial number, and the error path listing available
  serials.
- GenICam feature read/write across all three visibility levels (`simple`,
  `intermediate`, `advanced`).
- The attribute snapshot (`get_attributes_as_dict`) skipping features that
  are advertised as readable but raise when actually read.
- Single-frame `snap()`.
- Free-running streaming, including 12 frames recycled through a 3-buffer
  pool.
- Abort during acquisition returning promptly with state left consistent.
- 33 unit tests, including with `gi` unavailable.

**Not yet validated:**

- **End-to-end operation inside BLACS.** The tab, manual-mode Snap,
  continuous acquisition, and a buffered shot actually writing images to the
  shot file have not been exercised in the running BLACS GUI. Use
  `testing/connection_table_aravis_test.py` to do this without any other
  hardware connected.
- **Hardware-triggered acquisition.** The test camera has no trigger line
  wired to a pseudoclocked digital output. The trigger code path is
  inherited unchanged from `IMAQdxCamera`, and the camera-side trigger
  configuration is set entirely through `camera_attributes` — but neither
  has actually been exercised end to end. Before relying on triggered shots:
  1. Run `arv-tool-0.8 features` and confirm the expected `TriggerSource` —
     FLIR USB3 models use `Line0` for the opto-isolated input.
  2. Run a shot with `TriggerMode='On'` and confirm it acquires exactly as
     many frames as there were `expose()` calls, rather than timing out.

## Troubleshooting

Run the standalone smoke test first — it isolates Aravis from labscript and
BLACS. From `/home/ultracold/labscript-suite/userlib/user_devices`:

```bash
../../.labscript/bin/python AravisCamera/testing/aravis_smoke_test.py
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
