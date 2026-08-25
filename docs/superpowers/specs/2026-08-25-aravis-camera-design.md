# AravisCamera — design

**Date:** 2026-08-25
**Status:** Approved, ready for implementation planning
**Target:** `userlib/user_devices/AravisCamera/`

## Purpose

Drive FLIR USB3 Vision cameras from labscript on Linux via the Aravis GenICam
library.

The existing `MOT_Counting` camera in the HQA connection table is an
`IMAQdxCamera` with serial `1E1001551991` (`1E10` is the FLIR/Point Grey vendor
prefix). NI-IMAQdx is Windows-only, so that camera cannot be driven on this
machine at all. `labscript_devices` also ships `SpinnakerCamera`, but FLIR's
PySpin distributes prebuilt binaries pinned to specific Python and Ubuntu
versions and has none for Python 3.14 on Ubuntu 26.04.

Aravis is an actively maintained, vendor-neutral GenICam implementation
packaged by Ubuntu, and it speaks the same USB3 Vision protocol these cameras
already implement. No Aravis driver exists in `labscript_devices` today.

## Scope

**In scope:** a generic `AravisCamera` device driving any GenICam camera Aravis
supports; hardware-triggered buffered shots and software-triggered manual mode;
Mono8 and Mono16 pixel formats; the four FLIR families on site — Chameleon3,
Grasshopper3, Firefly, Blackfly (all vendor `1e10`, all USB3 Vision).

**Out of scope:** packed pixel formats (`Mono12Packed` and similar); colour
formats; GigE-specific tuning (packet size, inter-packet delay); chunk data;
multi-camera synchronisation beyond what shared trigger wiring already gives.

**Naming:** the device is `AravisCamera`, not `FLIRCamera`. Aravis is
vendor-neutral; the same driver will drive Basler, Allied Vision, or IDS
cameras. FLIR is the first tested vendor, not a constraint.

## Verified environment facts

Established by inspection on 2026-08-25:

- labscript-suite at `/home/ultracold/labscript-suite`, venv `.labscript`,
  Python 3.14.4, Ubuntu 26.04 (resolute).
- Active labconfig is `ultracold-HP-EliteDesk-800-G6-Tower-PC.ini`,
  `apparatus_name = HQA`. `example.ini` is a Windows leftover (`shared_drive = C:`).
- Aravis 0.8.34 is in Ubuntu universe: `libaravis-0.8-0`,
  `gir1.2-aravis-0.8`, `aravis-tools-cli`.
- `python3-gi` 3.56.2 is installed for the same CPython 3.14, but the venv sets
  `include-system-site-packages = false`, so `gi` is not importable in labscript.
- **`pip install pygobject` into the venv is not viable.** PyGObject publishes
  no binary wheels (`pip download --only-binary=:all:` reports "from versions:
  none" across all 51 releases), and this machine has no `gcc`, `cc`, `meson`,
  `ninja`, `pkg-config`, or dev headers, so the source build cannot start. This
  is moot regardless: `gi` contains no Aravis bindings — it reads
  `Aravis-0.8.typelib`, which only apt provides. There is no pip-only path.
- Verified working: with `PYTHONPATH=/usr/lib/python3/dist-packages`, the venv
  interpreter imports `gi` 3.56.2 and `GLib` 2.88 while venv packages (numpy)
  still take precedence.
- A FLIR camera is connected: USB `1e10:4000`, product string `U3V camera`
  (USB3 Vision — the protocol Aravis supports). Currently on a **480 Mbps**
  bus; should be moved to a USB3 port for full bandwidth.
- `usbfs_memory_mb` is 16 (kernel default); USB3 Vision needs ~1000.
- `/dev/bus/usb/001/033` is `crw-rw-r-- root root` — the user has read but not
  write access. User is in `plugdev`. A udev rule is required.
- `usbcore` is **built into the kernel**, so `/etc/modprobe.d` cannot set its
  parameters. `/etc/tmpfiles.d` exists and is the clean alternative.
- `userlib/user_devices` is a git repo on `main` tracking `origin/main`.
- `userlib/user_devices/DCAMCamera` is a working in-tree example of the same
  subclassing pattern and is the structural template for this work.

## Approach

Thin subclass of the `IMAQdxCamera` stack — the pattern used by both
`DCAMCamera` (in this repo) and `SpinnakerCamera` (upstream).

All shot sequencing, trigger arming, h5 writing to
`images/<orientation>/<name>/<frametype>`, zmq image transport to the BLACS
tab, attribute save/restore, the attributes dialog, and manual-mode snapping
are inherited unchanged. This keeps existing lyse analysis working and confines
new code to the Aravis binding itself.

Two approaches were rejected. A standalone driver not based on `IMAQdxCamera`
would reimplement ~800 lines of tested shot logic and risk drifting from the h5
layout lyse depends on. A separate Aravis server process communicating over zmq
(mirroring the old `imaqdx_server.py`) would guard against a GLib/Qt event-loop
clash, but BLACS workers already run as their own zprocess-spawned processes
separate from the Qt GUI, and Aravis can be driven synchronously via
`stream.timeout_pop_buffer()` with no GLib main loop. It is kept as a
contingency only if a threading problem appears.

## System prerequisites

One-time host changes, outside the Python code. These belong in the package
README.

**1. Install Aravis**

```bash
sudo apt install libaravis-0.8-0 gir1.2-aravis-0.8 aravis-tools-cli
```

`aravis-tools-cli` provides `arv-tool-0.8` for discovering serial numbers and
feature names independently of labscript.

**2. Expose `gi` to the venv**

```bash
ln -s /usr/lib/python3/dist-packages/gi \
      /home/ultracold/labscript-suite/.labscript/lib/python3.14/site-packages/gi
ln -s /usr/lib/python3/dist-packages/PyGObject-3.56.2.dist-info \
      /home/ultracold/labscript-suite/.labscript/lib/python3.14/site-packages/PyGObject-3.56.2.dist-info
```

Symlinking only PyGObject, rather than adding all 194 system dist-packages to
`sys.path`, avoids shadowing concerns (the system ships a second `PIL`, among
others) and is reversible with `rm`.

**3. udev rule** — `/etc/udev/rules.d/80-aravis-flir.rules`

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="1e10", MODE="0660", GROUP="plugdev"
```

The user is already in `plugdev`, so no group change or logout is needed.
`GROUP="plugdev"` is preferred over the commonly posted `MODE="0666"`: same
result here, without making the camera world-writable. Vendor `1e10` covers all
four camera families on site.

Apply with `sudo udevadm control --reload-rules && sudo udevadm trigger`, or
replug the camera.

**4. usbfs buffer limit** — `/etc/tmpfiles.d/usbfs-aravis.conf`

```
w /sys/module/usbcore/parameters/usbfs_memory_mb - - - - 1000
```

Survives reboots without editing GRUB. Apply immediately with
`sudo systemd-tmpfiles --create`. The GRUB alternative
(`usbcore.usbfs_memory_mb=1000` on the kernel command line) is the fallback if
tmpfiles proves unreliable.

## Migration notes

- **Serial numbers change, and their interpretation changes too.** The
  connection table's `1E1001551991` is IMAQdx's vendor-prefixed *hex* format.
  Aravis reports the GenICam `DeviceSerialNumber`, which for FLIR is a plain
  decimal string. Re-read real values with `arv-tool-0.8`. A serial-not-found
  error lists every serial actually detected, so discovery can happen from
  inside BLACS. See "Serial number handling" for why this requires an
  `__init__` override rather than being a documentation-only concern.
- **Attribute names lose their category prefixes.** IMAQdx presents GenICam
  features as `Category::Feature`; Aravis uses bare names. `camera_attributes`
  accepts **bare GenICam names only** — no prefix stripping, no category map.
  This was chosen deliberately over accepting both forms: it matches what
  `arv-tool-0.8` reports natively, so what you read from the tool is what you
  write in the connection table.
- **Move the camera to a USB3 port.** It enumerates and streams on USB2, but at
  a fraction of its framerate.

## Package structure

```
userlib/user_devices/AravisCamera/
├── __init__.py                    # empty, matches DCAMCamera
├── README.md                      # prerequisites, usage, troubleshooting
├── labscript_devices.py           # AravisCamera(IMAQdxCamera)
├── blacs_tabs.py                  # AravisCameraTab(IMAQdxCameraTab)
├── blacs_workers.py               # Aravis_Camera + AravisCameraWorker
├── register_classes.py            # registers the tab
└── testing/
    ├── aravis_smoke_test.py       # standalone, runs outside BLACS
    └── connection_table_aravis_test.py   # minimal all-dummy test table
```

### `labscript_devices.py`

`AravisCamera(IMAQdxCamera)` overriding `description = 'Aravis GenICam Camera'`
and `__init__` (see "Serial number handling" below — this is the one place the
base class is actively wrong for Aravis).

It keeps the full signature — `serial_number`, `orientation`, `pixel_size`,
`magnification`, `trigger_edge_type`, `trigger_duration`,
`minimum_recovery_time`, `camera_attributes`, `manual_mode_camera_attributes`,
`stop_acquisition_timeout`, `exception_on_failed_shot`,
`saved_attribute_visibility_level`, `mock`. No new parameters: everything
Aravis needs is expressible as GenICam attributes.

### Serial number handling

`IMAQdxCamera.__init__` contains:

```python
if isinstance(serial_number, (str, bytes)):
    serial_number = int(serial_number, 16)
self.serial_number = serial_number
self.BLACS_connection = hex(self.serial_number)[2:].upper()
```

**String serial numbers are parsed as hexadecimal.** That is correct for
IMAQdx, whose serials genuinely are hex (hence `1E1001551991` in the existing
connection table). Aravis reports the GenICam `DeviceSerialNumber`, an opaque
string that for FLIR is **decimal**. Inheriting this behaviour would silently
reinterpret `"15551991"` as hex — a different number, with no error raised, and
a BLACS tab labelled with a meaningless value.

`AravisCamera` therefore overrides `__init__`: it stores `serial_number`
verbatim as a string, sets `BLACS_connection` to that same string so the BLACS
tab shows the real serial, replicates the remaining ~20 lines of
`IMAQdxCamera.__init__` validation (the `manual_mode_camera_attributes` subset
check and the `saved_attribute_visibility_level` check), and calls
`TriggerableDevice.__init__` directly.

This is deliberately an explicit override rather than a post-hoc fixup of
`self.serial_number` after calling `super().__init__()`. Both `IMAQdxCamera`
and any override are decorated with `@set_passed_properties`, and relying on
which decorator's captured value ultimately lands in
`connection_table_properties` would be fragile. The duplicated validation is
stable upstream code and the override is explicit about what it changes.

The worker compares serials as strings against
`Aravis.get_device_serial_nbr(i)`, so no further conversion is needed.

*Passing an `int` would coincidentally work* — the hex branch only triggers for
`str`/`bytes` — but it produces a nonsensical `BLACS_connection` and breaks for
vendors with alphanumeric serials. It is not the chosen approach.

### `register_classes.py`

```python
register_classes('AravisCamera',
    BLACS_tab='user_devices.AravisCamera.blacs_tabs.AravisCameraTab',
    runviewer_parser=None)
```

### `blacs_tabs.py`

`AravisCameraTab(IMAQdxCameraTab)` overriding only `worker_class`. Unlike
`DCAMCameraTab`, it does **not** override `initialise_workers` — that override
exists in DCAMCamera solely to pass its extra `occupation_receiver_port`, which
has no analogue here. The stock implementation is inherited.

### `blacs_workers.py`

`AravisCameraWorker(IMAQdxCameraWorker)` sets `interface_class = Aravis_Camera`
and adds nothing else. All logic lives in `Aravis_Camera`, described below.

## The `Aravis_Camera` binding

The only substantial new code. It must satisfy the contract
`IMAQdxCameraWorker` calls into:

| Member | Notes |
|---|---|
| `__init__(serial_number)` | |
| `exception_on_failed_shot` | attribute; worker writes to it |
| `_abort_acquisition` | attribute; worker resets it to `False` in `abort()` |
| `set_attributes(dict)` | |
| `get_attribute(name)` | |
| `get_attribute_names(visibility_level)` | called with a string level |
| `snap()` | returns a 2D numpy array |
| `configure_acquisition(continuous, bufferCount)` | **also starts acquisition** |
| `grab()` | single image; used by the continuous manual-mode loop |
| `grab_multiple(n_images, images)` | appends to `images`; must honour `_abort_acquisition` |
| `stop_acquisition()` | |
| `abort_acquisition()` | sets `_abort_acquisition = True` |
| `close()` | |

### Import guard

`gi` and Aravis are imported lazily inside `__init__`, as `SpinnakerCamera`
does with PySpin:

```python
gi.require_version('Aravis', '0.8')
from gi.repository import Aravis
```

This keeps the module importable on machines without Aravis, so `mock=True`
works and runmanager can parse the connection table anywhere.

### Discovery by serial

`Aravis.update_device_list()`, then walk `Aravis.get_n_devices()` comparing
`Aravis.get_device_serial_nbr(i)` against the requested serial (compared as
strings), and open the match with
`Aravis.Camera.new(Aravis.get_device_id(i))`.

On no match, raise an exception listing **every serial detected**.

A serial of `'Fake_1'` calls `Aravis.enable_interface('Fake')` first and opens
Aravis' built-in fake GenICam device.

### Attribute access by GenICam type

Via `camera.get_device()`, fetch the node with `device.get_feature(name)` and
dispatch on node class:

| Node type | Accessor |
|---|---|
| `GcEnumeration`, `GcString` | `get`/`set_string_feature_value` |
| `GcInteger` | `get`/`set_integer_feature_value` |
| `GcFloat` | `get`/`set_float_feature_value` |
| `GcBoolean` | `get`/`set_boolean_feature_value` |
| `GcCommand` | `execute_command`; write-only, skipped when reading |

Aravis raises `GLib.Error` on failure. Catch it and re-raise with the feature
name and attempted value attached, matching the IMAQdx error style
(`"failed to set attribute {name} to {value}"`).

### Interdependent attributes: one retry pass

Setting `Width` before `OffsetX` can fail when the existing offset plus the new
width exceeds the sensor — the `MOT_Counting` ROI dict is exactly this shape.

`set_attributes` applies features in dict order (Python preserves insertion
order, so the user retains control), collects failures instead of raising
immediately, then retries the failed set once. Only a feature that fails twice
raises. This resolves the common ordering interdependencies without a
hand-maintained dependency graph.

### Visibility mapping

Aravis exposes GenICam's native visibility, which maps onto
`saved_attribute_visibility_level`:

| labscript level | GenICam visibilities |
|---|---|
| `'simple'` | BEGINNER |
| `'intermediate'` | BEGINNER, EXPERT |
| `'advanced'` | BEGINNER, EXPERT, GURU |

`get_attribute_names` walks the node tree from `Root` through `GcCategory`
nodes, keeping features that are available, implemented, readable, and within
the requested visibility. Command nodes are excluded.

### Acquisition and buffers

`configure_acquisition(continuous, bufferCount)` creates the stream with
`camera.create_stream(None, None)`, allocates `bufferCount` buffers of
`camera.get_payload()` bytes via `Aravis.Buffer.new_allocate()`, pushes them to
the stream, and calls `camera.start_acquisition()`. It **starts** acquisition,
matching the IMAQdx semantics `transition_to_buffered` relies on.

Both `continuous=True` (manual-mode live view) and `continuous=False` (buffered
shot) use GenICam `AcquisitionMode='Continuous'`; the camera streams frames as
they are triggered in either case. The parameter's only effect is the size of
the buffer pool — the worker passes `bufferCount=n_images` for a shot and the
default pool for live view. `continuous` is accepted for signature
compatibility with the base class and does not change the acquisition mode.

`snap()` acquires a single frame. Aravis provides
`camera.acquisition(timeout_us)` as a one-shot convenience that handles stream
setup and teardown internally, which is the natural implementation; it returns
a buffer converted by the same path described below.

`grab_multiple` is the shot-critical loop. It uses
`stream.timeout_pop_buffer(timeout_us)` rather than a blocking pop, so every
timeout is an opportunity to check `_abort_acquisition` — this is what keeps
the BLACS abort button responsive while the camera waits on its trigger line.
On each buffer: verify `get_status() == Aravis.BufferStatus.SUCCESS`, convert,
append, and **push the buffer back** to the stream. A non-SUCCESS status
honours `exception_on_failed_shot` the same way IMAQdx does.

### Buffer to numpy

Bytes per pixel is computed as `len(data) // (width * height)`, the same
approach `IMAQdx_Camera._decode_image_data` uses. This supports Mono8 and
Mono16 with no pixel-format table to maintain.

The array **must** be `.copy()`d out before the buffer is recycled: the worker
later sends images over zmq with `copy=False`, and the underlying Aravis buffer
would be overwritten by subsequent frames.

Packed formats (`Mono12Packed`) will not decode correctly under this scheme and
are explicitly out of scope. Use `Mono8` or `Mono16` — which is what the
existing `MOT_Counting` entry already specifies.

### API verification (first implementation step)

The streaming API above is confirmed against Aravis' own upstream test
(`tests/fake.py`): `Camera.new`, `create_stream`, `Buffer.new_allocate`,
`push_buffer`, `pop_buffer`, `get_status`, `get_data`, `get_payload`,
`start_acquisition`, `stop_acquisition`.

The feature-access method names are specified from the documented C API on the
basis that the GI binding drops the `arv_device_` prefix. **Before writing
against them, introspect the installed 0.8.34 typelib** (`dir(Aravis.Device)`,
`dir(Aravis.Camera)`) and confirm exact names. Note that upstream `tests/fake.py`
targets Aravis 0.10 while Ubuntu ships 0.8 — use
`gi.require_version('Aravis', '0.8')` and expect minor API differences from
the upstream examples.

## Connection table usage

The existing `MOT_Counting` entry, translated:

```python
from user_devices.AravisCamera.labscript_devices import AravisCamera

AravisCamera(
    "MOT_Counting",
    parent_device=DIO32_1,
    connection="21",
    serial_number="<from arv-tool-0.8>",
    orientation="PoC6",
    exception_on_failed_shot=False,
    camera_attributes={
        'ExposureAuto':      'Off',
        'ExposureTime':      mot_counting_exposure_duration_connection_table,
        'GainAuto':          'Off',
        'Gain':              0,
        'TriggerMode':       'On',
        'TriggerSource':     'Line0',
        'TriggerSelector':   'FrameStart',
        'TriggerActivation': 'RisingEdge',
        'AcquisitionMode':   'Continuous',
        'PixelFormat':       'Mono16',
        'Width':             image_roi_width,
        'Height':            image_roi_height,
        'OffsetX':           image_roi_offset_x,
        'OffsetY':           image_roi_offset_y,
    },
    manual_mode_camera_attributes={
        'TriggerMode': 'Off',
    },
)
```

Structurally identical to the existing entry; only the `Category::` prefixes
drop away, and `TriggerSource` / `TriggerSelector` / `AcquisitionMode` are
stated explicitly rather than relying on camera-side defaults.

`expose()`, `frametype`, the h5 layout, and lyse access are inherited
unchanged, so existing analysis code keeps working.

## Shot flow and the hardware/software trigger split

Flow is entirely inherited; restated so the trigger requirements are explicit.

1. `transition_to_buffered` reads `EXPOSURES` from the h5 file to get
   `n_images`, applies `camera_attributes` through the smart cache, snapshots
   attributes for saving, calls
   `configure_acquisition(continuous=False, bufferCount=n_images)`, and starts
   `grab_multiple` on a daemon thread. The camera is now armed, waiting on its
   trigger line.
2. The shot runs. Each `camera.expose(t, ...)` emits a trigger pulse from the
   parent device at time `t`.
3. `transition_to_manual` joins the acquisition thread with
   `stop_acquisition_timeout`, stops acquisition, and writes images to
   `images/<orientation>/<name>/<frametype>` as gzipped `uint16`, setting
   `failed_shot` if fewer images arrived than exposures were requested.

The requested "hardware for shots, software for manual mode" behaviour needs no
new code. It follows from two existing parameters:

- `camera_attributes` (applied for buffered shots) sets `TriggerMode='On'` with
  the trigger source, selector, and activation.
- `manual_mode_camera_attributes` (applied at startup and after every shot)
  sets `TriggerMode='Off'`.

The camera therefore hardware-triggers during shots and free-runs for
alignment, switching automatically at each transition.

`Line0` is the opto-isolated input on FLIR USB3 models; confirm against actual
wiring with `arv-tool-0.8 features`.

## Error handling

Each failure mode produces a message naming its fix.

| Failure | Response |
|---|---|
| `gi` not importable | Points at the README symlink step |
| Aravis typelib missing | Points at `apt install gir1.2-aravis-0.8` |
| Serial not found | Raises listing every serial detected |
| USB permission denied | Points at the udev rule |
| Buffer status ≠ `SUCCESS` | Honours `exception_on_failed_shot` |
| Triggers never arrive | Inherited timeout: *"Check triggering is connected/configured correctly"* |

Low `usbfs_memory_mb` typically manifests as buffer-allocation failures or
incomplete frames rather than a clear error; the README troubleshooting section
must name this symptom explicitly.

## Testing

**Constraints.** Two, both hard:

1. The camera is not wired to a parent device for triggering, so
   hardware-triggered shots cannot be validated in this work.
2. **The camera is the only device connected to this PC.** No ADwin, no RFSoC,
   no DDS, no SLM. The production `HQA` connection table cannot be loaded in
   BLACS at all, because every other device would fail to connect.

### Test connection table

Testing therefore runs against a dedicated minimal connection table containing
nothing but dummy devices and the camera. `labscript_devices` ships
`DummyPseudoclock` and `DummyIntermediateDevice` for exactly this purpose —
`DummyPseudoclock` is documented as usable as the sole device in a connection
table.

Shipped as `AravisCamera/testing/connection_table_aravis_test.py` so it is
version-controlled alongside the driver (note `userlib/labscriptlib` is not a
git repository):

```python
from labscript import start, stop
from labscript_devices.DummyPseudoclock.labscript_devices import DummyPseudoclock
from labscript_devices.DummyIntermediateDevice import DummyIntermediateDevice
from user_devices.AravisCamera.labscript_devices import AravisCamera

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
    serial_number='<from arv-tool-0.8>',
    orientation='test',
    trigger_duration=1e-3,
    exception_on_failed_shot=False,
    camera_attributes={
        'TriggerMode':  'Off',      # free-run: no trigger wire exists
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
    stop(1)
```

Mechanics confirmed by inspection:

- `TriggerableDevice.__init__` auto-creates a `Trigger` when the parent is an
  intermediate device rather than an existing `Trigger`, so
  `parent_device=dummy_device, connection='camera_trigger'` is sufficient.
- `Trigger` subclasses `DigitalOut`, which is in
  `DummyIntermediateDevice.allowed_children`.
- `IMAQdxCamera.__init__` rejects any key in `manual_mode_camera_attributes`
  that is absent from `camera_attributes`, so `TriggerMode` must appear in
  both — as it does above.

To use it, temporarily point `connection_table_py` in
`labconfig/ultracold-HP-EliteDesk-800-G6-Tower-PC.ini` at this file and
recompile the connection table. The production `HQA` table is left untouched;
restoring is a one-line revert.

To exercise the labscript/BLACS plumbing with **no hardware at all**, the same
file with `mock=True` on the camera needs nothing connected.

### Tiers

All tiers below run against the test connection table above, never the
production `HQA` table.

| Tier | Requires | Validates |
|---|---|---|
| 0. `aravis_smoke_test.py` | Aravis + camera | Aravis alone, outside labscript entirely |
| 1. `mock=True` | Nothing connected | Registration, BLACS tab, h5 layout, lyse access |
| 2. `serial_number='Fake_1'` | Aravis installed | The real binding: discovery, typed attribute get/set, streaming, buffer→numpy |
| 3. Firefly, manual mode | Camera + udev/usbfs | Enumeration by serial, live snap, feature read/write, continuous view |
| 4. Firefly, free-run buffered shot | Camera only, **no trigger wire** | The whole buffered path end-to-end |
| 5. Firefly, hardware-triggered shot | **Blocked — not wired** | Trigger timing only |

Tiers 0–2 need no camera-specific hardware setup at all, so implementation can
proceed and be largely validated before touching udev or usbfs.

Tier 4 is the key one neither constraint blocks. The test table's
`TriggerMode='Off'` makes the camera free-run, so `grab_multiple` collects
`n_images` as fast as the sensor delivers them without any trigger wire. This
exercises arming, the abort-checking pop loop, buffer recycling, the
acquisition thread, the timeout path, h5 writing, and lyse readback — the
entire buffered machinery. The only thing left unvalidated is whether frames
land at the correct times.

The production configuration under "Connection table usage" differs from the
test table in exactly one respect: `TriggerMode='On'` plus the trigger source,
selector, and activation. That is the delta that stays untested until wiring
exists.

`testing/aravis_smoke_test.py` runs standalone outside BLACS: enumerate
devices, open by serial, print features by visibility, grab a frame, report
shape and dtype. This makes Aravis debuggable without the BLACS stack in the
way.

### Known limitation carried forward

**Hardware triggering ships as designed-but-unvalidated.** The code path exists
and is correct by construction, but remains untested until a trigger line is
wired to a pseudoclocked digital output. Tier 5 is a documented follow-up, not
part of this work.

A `parent_device` is still required even while unwired, because `IMAQdxCamera`
mandates one to attach `expose()` to. Pointing it at a spare digital output is
harmless: with nothing connected, it emits pulses into open air. The dead
`MOT_Counting` entry already declares `DIO32_1` connection `"21"`, which is an
available slot to reuse.

## Deliverables

1. `AravisCamera/__init__.py`
2. `AravisCamera/labscript_devices.py`
3. `AravisCamera/blacs_tabs.py`
4. `AravisCamera/blacs_workers.py` — `Aravis_Camera` and `AravisCameraWorker`
5. `AravisCamera/register_classes.py`
6. `AravisCamera/README.md` — the four prerequisites above, connection table
   usage, and troubleshooting covering the usbfs symptom, udev permissions, and
   serial-number discovery
7. `AravisCamera/testing/aravis_smoke_test.py`
8. `AravisCamera/testing/connection_table_aravis_test.py` — minimal all-dummy
   connection table, the only table any tier is tested against
