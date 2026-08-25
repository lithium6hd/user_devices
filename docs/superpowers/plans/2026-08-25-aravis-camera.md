# AravisCamera Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `AravisCamera` labscript device that drives FLIR USB3 Vision cameras on Linux through the Aravis GenICam library, as a thin subclass of the `IMAQdxCamera` stack.

**Architecture:** `AravisCamera` subclasses `IMAQdxCamera`, `AravisCameraTab` subclasses `IMAQdxCameraTab`, and `AravisCameraWorker` subclasses `IMAQdxCameraWorker` with `interface_class = Aravis_Camera`. All shot sequencing, h5 writing, and image transport are inherited unchanged. The only substantial new code is `Aravis_Camera`, which adapts Aravis to the ~10-method interface the base worker calls into. Pure logic (buffer decoding, attribute application, visibility mapping) lives in a dependency-free `aravis_utils.py` so it can be unit-tested before Aravis is installed.

**Tech Stack:** Python 3.14.4, Aravis 0.8.34 via GObject Introspection (PyGObject 3.56.2), numpy, h5py, labscript-suite 3.3.0, pytest.

**Spec:** `docs/superpowers/specs/2026-08-25-aravis-camera-design.md`

## Global Constraints

- **Branch:** all work happens on `aravis-camera` in `/home/ultracold/labscript-suite/userlib/user_devices`. Never commit to `main`.
- **Do not touch** the unrelated uncommitted work in this repo: `DDS_AS065`, `PasquansLock/`, `Rigol_DG4000/`.
- **Python interpreter is always** `/home/ultracold/labscript-suite/.labscript/bin/python`. Never `/usr/bin/python3`.
- **Aravis API version is `0.8`**, not 0.10. Always `gi.require_version('Aravis', '0.8')`. Upstream Aravis examples target 0.10 and may differ.
- **Attribute names are bare GenICam names only** — `'ExposureTime'`, never `'AcquisitionControl::ExposureTime'`.
- **Pixel formats supported: `Mono8` and `Mono16` only.** Packed formats such as `Mono12Packed` are out of scope and must raise a clear error, not decode silently.
- **Import order:** in any module that uses h5py, `import labscript_utils.h5_lock` **before** `import h5py`, or import fails with "h5_lock must be imported before any code imports h5py".
- **Serial numbers are opaque strings.** Never call `int(serial, 16)` on them.
- **Testing uses only the dummy connection table** in `AravisCamera/testing/`. The production `HQA` connection table must never be loaded — no other hardware is connected to this machine.
- **Hardware triggering cannot be validated.** The camera has no trigger wire. Do not write tests that assume triggered acquisition works.

---

## File Structure

| File | Responsibility |
|---|---|
| `AravisCamera/__init__.py` | Empty package marker |
| `AravisCamera/aravis_utils.py` | Pure helpers: buffer decoding, attribute application with retry, serial matching, visibility mapping. Imports only `numpy`. No Aravis, no blacs. |
| `AravisCamera/labscript_devices.py` | `AravisCamera(IMAQdxCamera)` — device class, serial handling |
| `AravisCamera/blacs_workers.py` | `Aravis_Camera` (Aravis binding) + `AravisCameraWorker` |
| `AravisCamera/blacs_tabs.py` | `AravisCameraTab(IMAQdxCameraTab)` |
| `AravisCamera/register_classes.py` | Device registration |
| `AravisCamera/README.md` | Install prerequisites, usage, troubleshooting |
| `AravisCamera/testing/conftest.py` | pytest fixtures |
| `AravisCamera/testing/test_aravis_utils.py` | Unit tests for pure helpers |
| `AravisCamera/testing/test_labscript_devices.py` | Unit tests for the device class |
| `AravisCamera/testing/aravis_smoke_test.py` | Standalone Aravis check, runs outside BLACS |
| `AravisCamera/testing/connection_table_aravis_test.py` | Minimal all-dummy connection table |

**Note on `aravis_utils.py`:** `DCAMCamera` keeps everything in `blacs_workers.py`. This plan deviates deliberately: the pure logic is the only unit-testable part, and isolating it from the `blacs` and `Aravis` imports lets Tasks 2–3 be completed and tested **before** Aravis is installed in Task 4.

---

### Task 1: Package skeleton and `AravisCamera` device class

Establishes the package, the test harness, and the one place the base class is actively wrong for Aravis: serial number parsing.

**Files:**
- Create: `AravisCamera/__init__.py`
- Create: `AravisCamera/labscript_devices.py`
- Create: `AravisCamera/testing/conftest.py`
- Test: `AravisCamera/testing/test_labscript_devices.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `AravisCamera(name, parent_device, connection, serial_number, orientation=None, pixel_size=[1.0,1.0], magnification=1.0, trigger_edge_type='rising', trigger_duration=None, minimum_recovery_time=0.0, camera_attributes=None, manual_mode_camera_attributes=None, stop_acquisition_timeout=5.0, exception_on_failed_shot=True, saved_attribute_visibility_level='intermediate', mock=False, **kwargs)`. Guarantees `self.serial_number` is a `str` and `connection_table_properties['serial_number']` round-trips as that same `str`.

- [ ] **Step 1: Install pytest into the labscript venv**

```bash
cd /home/ultracold/labscript-suite
.labscript/bin/pip install pytest
```

Expected: installs `pytest`, `pluggy`, `iniconfig`, `packaging`, `pygments` as pure wheels. No compiler required.

- [ ] **Step 2: Create the package directories and empty marker**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git checkout aravis-camera
mkdir -p AravisCamera/testing
touch AravisCamera/__init__.py
```

- [ ] **Step 3: Write the pytest fixture file**

`AravisCamera/testing/conftest.py`:

```python
"""pytest fixtures for AravisCamera tests.

labscript_utils.h5_lock MUST be imported before h5py anywhere in the process,
so it is imported here first, before anything else pulls h5py in.
"""
import labscript_utils.h5_lock  # noqa: F401  (must precede h5py)

import pytest


@pytest.fixture
def labscript_shot(tmp_path):
    """Initialise a fresh labscript shot in a temp dir.

    Returns the path of the h5 file. labscript keeps global compiler state, so
    each test gets a fresh init.
    """
    import labscript

    h5_path = str(tmp_path / "test_shot.h5")
    labscript.labscript_init(h5_path, new=True, overwrite=True)
    yield h5_path
    # Tear down labscript's global state so the next test starts clean.
    labscript.labscript_cleanup()


@pytest.fixture
def dummy_parent(labscript_shot):
    """A DummyPseudoclock + DummyIntermediateDevice to hang a camera off.

    Returns the DummyIntermediateDevice instance.
    """
    from labscript_devices.DummyPseudoclock.labscript_devices import DummyPseudoclock
    from labscript_devices.DummyIntermediateDevice import DummyIntermediateDevice

    clock = DummyPseudoclock(name="dummy_clock", BLACS_connection="dummy")
    return DummyIntermediateDevice(
        name="dummy_device",
        parent_device=clock.clockline,
        BLACS_connection="dummy2",
    )
```

- [ ] **Step 4: Write the failing test**

`AravisCamera/testing/test_labscript_devices.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they fail**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/test_labscript_devices.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'user_devices.AravisCamera.labscript_devices'`

- [ ] **Step 6: Write the implementation**

`AravisCamera/labscript_devices.py`:

```python
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
        the GenICam ``DeviceSerialNumber``, an opaque string which for FLIR
        cameras is decimal. ``IMAQdxCamera`` parses string serials as
        *hexadecimal*, which would silently address the wrong camera, so this
        class does not call ``IMAQdxCamera.__init__`` and instead reproduces
        its setup with the hex conversion removed.
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
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/test_labscript_devices.py -v
```

Expected: 5 passed.

If `test_invalid_visibility_level_is_rejected` fails on the `match=` pattern, check the exact message text produced by the `%` formatting and adjust the regex — do not weaken the assertion to `pytest.raises(ValueError)` alone.

- [ ] **Step 8: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/__init__.py AravisCamera/labscript_devices.py \
        AravisCamera/testing/conftest.py \
        AravisCamera/testing/test_labscript_devices.py
git commit -m "feat(AravisCamera): add device class with string serial numbers"
```

---

### Task 2: Buffer decoding helpers

Converts raw Aravis buffer bytes into numpy arrays. Pure logic — no Aravis needed, so this can be done before the library is installed.

**Files:**
- Create: `AravisCamera/aravis_utils.py`
- Test: `AravisCamera/testing/test_aravis_utils.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `bytes_per_pixel(n_bytes: int, width: int, height: int) -> int` — returns 1 or 2, raises `ValueError` otherwise
  - `decode_buffer(data: bytes, width: int, height: int) -> numpy.ndarray` — shape `(height, width)`, dtype `uint8` or `uint16`, always a copy that owns its memory

- [ ] **Step 1: Write the failing test**

`AravisCamera/testing/test_aravis_utils.py`:

```python
"""Unit tests for AravisCamera pure helpers.

These deliberately import nothing from Aravis or blacs, so they run on a
machine with no camera and no Aravis installation.
"""
import numpy as np
import pytest

from user_devices.AravisCamera.aravis_utils import (
    bytes_per_pixel,
    decode_buffer,
)


def test_bytes_per_pixel_mono8():
    assert bytes_per_pixel(100 * 50, 100, 50) == 1


def test_bytes_per_pixel_mono16():
    assert bytes_per_pixel(2 * 100 * 50, 100, 50) == 2


def test_bytes_per_pixel_rejects_packed_formats():
    """Mono12Packed gives 1.5 bytes/pixel and must fail loudly rather than
    silently producing a garbled image."""
    n_bytes = 3 * (100 * 50) // 2
    with pytest.raises(ValueError, match="Mono8 or Mono16"):
        bytes_per_pixel(n_bytes, 100, 50)


def test_bytes_per_pixel_rejects_wrong_size():
    with pytest.raises(ValueError):
        bytes_per_pixel(7, 100, 50)


def test_bytes_per_pixel_rejects_zero_pixels():
    with pytest.raises(ValueError):
        bytes_per_pixel(0, 0, 0)


def test_decode_buffer_mono8_shape_and_dtype():
    width, height = 4, 3
    data = bytes(range(width * height))
    image = decode_buffer(data, width, height)
    assert image.shape == (height, width)
    assert image.dtype == np.uint8


def test_decode_buffer_mono8_values():
    width, height = 4, 3
    data = bytes(range(width * height))
    image = decode_buffer(data, width, height)
    expected = np.arange(width * height, dtype=np.uint8).reshape(height, width)
    np.testing.assert_array_equal(image, expected)


def test_decode_buffer_mono16_values():
    width, height = 4, 3
    expected = np.arange(width * height, dtype=np.uint16).reshape(height, width)
    image = decode_buffer(expected.tobytes(), width, height)
    assert image.dtype == np.uint16
    np.testing.assert_array_equal(image, expected)


def test_decode_buffer_returns_owned_copy():
    """The Aravis buffer is pushed back to the stream and overwritten by the
    next frame, and the worker later sends images over zmq with copy=False.
    The returned array must own its memory."""
    width, height = 4, 3
    data = bytes(width * height)
    image = decode_buffer(data, width, height)
    assert image.flags['OWNDATA']
    assert image.flags['WRITEABLE']
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/test_aravis_utils.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'user_devices.AravisCamera.aravis_utils'`

- [ ] **Step 3: Write the implementation**

`AravisCamera/aravis_utils.py`:

```python
#####################################################################
#                                                                   #
# /user_devices/AravisCamera/aravis_utils.py                        #
#                                                                   #
# Pure helpers for the AravisCamera driver.                         #
#                                                                   #
# This module imports only numpy: no Aravis, no gi, no blacs. That  #
# keeps it unit-testable on machines with neither Aravis nor a      #
# camera installed.                                                 #
#                                                                   #
#####################################################################
import numpy as np

#: Bytes per pixel -> numpy dtype. Only unpacked mono formats are supported.
_DTYPES = {1: np.uint8, 2: np.uint16}


def bytes_per_pixel(n_bytes, width, height):
    """Infer bytes per pixel from a buffer size and image dimensions.

    Args:
        n_bytes (int): size of the raw buffer.
        width (int): image width in pixels.
        height (int): image height in pixels.

    Returns:
        int: 1 for Mono8, 2 for Mono16.

    Raises:
        ValueError: if the buffer is not a whole number of 1- or 2-byte
            pixels. Packed formats such as Mono12Packed land here.
    """
    n_pixels = width * height
    if n_pixels <= 0:
        raise ValueError(
            f"cannot decode an image with {width}x{height} pixels"
        )
    bpp, remainder = divmod(n_bytes, n_pixels)
    if remainder or bpp not in _DTYPES:
        raise ValueError(
            f"cannot decode {n_bytes} bytes as a {width}x{height} image: "
            f"got {n_bytes / n_pixels:.4g} bytes per pixel, expected 1 or 2. "
            f"Packed pixel formats are not supported; use Mono8 or Mono16."
        )
    return bpp


def decode_buffer(data, width, height):
    """Convert raw Aravis buffer bytes into a 2D numpy array.

    The returned array is always a copy that owns its memory. This is
    required: the source buffer is pushed back to the Aravis stream and
    overwritten by the next frame, and the BLACS worker later transmits
    images over zmq with ``copy=False``.

    Args:
        data (bytes): raw image bytes from ``ArvBuffer.get_data()``.
        width (int): image width in pixels.
        height (int): image height in pixels.

    Returns:
        numpy.ndarray: shape ``(height, width)``, dtype uint8 or uint16.
    """
    dtype = _DTYPES[bytes_per_pixel(len(data), width, height)]
    return np.frombuffer(data, dtype=dtype).reshape(height, width).copy()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/test_aravis_utils.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/aravis_utils.py AravisCamera/testing/test_aravis_utils.py
git commit -m "feat(AravisCamera): add buffer decoding helpers"
```

---

### Task 3: Attribute application, serial matching, and visibility helpers

The remaining pure logic. Still no Aravis dependency.

**Files:**
- Modify: `AravisCamera/aravis_utils.py` (append)
- Modify: `AravisCamera/testing/test_aravis_utils.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `visibility_names(level: str) -> tuple[str, ...]` — maps `'simple'`/`'intermediate'`/`'advanced'` to GenICam visibility names
  - `find_serial(serials: list, wanted) -> int | None` — index of a matching serial, compared as strings
  - `apply_with_retry(setter: callable, attributes: dict) -> None` — applies `setter(name, value)` for each item, retrying failures once

- [ ] **Step 1: Write the failing test**

Append to `AravisCamera/testing/test_aravis_utils.py`:

```python
from user_devices.AravisCamera.aravis_utils import (
    apply_with_retry,
    find_serial,
    visibility_names,
)


def test_visibility_names_levels():
    assert visibility_names('simple') == ('BEGINNER',)
    assert visibility_names('intermediate') == ('BEGINNER', 'EXPERT')
    assert visibility_names('advanced') == ('BEGINNER', 'EXPERT', 'GURU')


def test_visibility_names_is_case_insensitive():
    assert visibility_names('Simple') == ('BEGINNER',)


def test_visibility_names_rejects_unknown_level():
    with pytest.raises(ValueError, match="invalid visibility level"):
        visibility_names('nonsense')


def test_find_serial_matches_string():
    assert find_serial(['111', '222', '333'], '222') == 1


def test_find_serial_compares_as_strings():
    """Aravis returns serials as strings; a user may write an int."""
    assert find_serial(['111', '222'], 222) == 1


def test_find_serial_returns_none_when_absent():
    assert find_serial(['111', '222'], '999') is None


def test_find_serial_handles_empty_list():
    assert find_serial([], '111') is None


class _RecordingSetter:
    """Test double for a camera attribute setter.

    Fails for names in ``fail_names`` until each has been attempted
    ``fail_times`` times, then succeeds.
    """

    def __init__(self, fail_names=(), fail_times=1):
        self.calls = []
        self.attempts = {}
        self.fail_names = set(fail_names)
        self.fail_times = fail_times

    def __call__(self, name, value):
        self.calls.append((name, value))
        self.attempts[name] = self.attempts.get(name, 0) + 1
        if name in self.fail_names and self.attempts[name] <= self.fail_times:
            raise RuntimeError(f"simulated failure setting {name}")


def test_apply_with_retry_applies_all_attributes_once():
    setter = _RecordingSetter()
    apply_with_retry(setter, {'A': 1, 'B': 2})
    assert setter.calls == [('A', 1), ('B', 2)]


def test_apply_with_retry_preserves_dict_order():
    """Users control GenICam ordering through dict insertion order."""
    setter = _RecordingSetter()
    apply_with_retry(setter, {'OffsetX': 0, 'Width': 100, 'OffsetY': 0})
    assert [name for name, _ in setter.calls] == ['OffsetX', 'Width', 'OffsetY']


def test_apply_with_retry_retries_a_failure_once():
    """The motivating case: setting Width before OffsetX can fail because the
    old offset plus the new width exceeds the sensor, then succeed on retry."""
    setter = _RecordingSetter(fail_names={'Width'}, fail_times=1)
    apply_with_retry(setter, {'Width': 100, 'OffsetX': 0})
    assert setter.attempts['Width'] == 2
    assert setter.attempts['OffsetX'] == 1


def test_apply_with_retry_raises_when_failure_persists():
    setter = _RecordingSetter(fail_names={'Bogus'}, fail_times=99)
    with pytest.raises(RuntimeError, match="Bogus"):
        apply_with_retry(setter, {'Bogus': 1})


def test_apply_with_retry_does_not_retry_successes():
    setter = _RecordingSetter(fail_names={'B'}, fail_times=1)
    apply_with_retry(setter, {'A': 1, 'B': 2, 'C': 3})
    assert setter.attempts['A'] == 1
    assert setter.attempts['C'] == 1


def test_apply_with_retry_accepts_empty_dict():
    setter = _RecordingSetter()
    apply_with_retry(setter, {})
    assert setter.calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/test_aravis_utils.py -v
```

Expected: FAIL — `ImportError: cannot import name 'apply_with_retry'`

- [ ] **Step 3: Write the implementation**

Append to `AravisCamera/aravis_utils.py`:

```python
#: labscript visibility level -> GenICam visibility names, least to most
#: obscure. Converted to Aravis enum members at call time so this module
#: stays free of Aravis imports.
_VISIBILITY_NAMES = {
    'simple': ('BEGINNER',),
    'intermediate': ('BEGINNER', 'EXPERT'),
    'advanced': ('BEGINNER', 'EXPERT', 'GURU'),
}


def visibility_names(level):
    """Map a labscript visibility level to GenICam visibility names.

    Args:
        level (str): 'simple', 'intermediate' or 'advanced'.

    Returns:
        tuple of str: GenICam visibility names, e.g. ``('BEGINNER', 'EXPERT')``.

    Raises:
        ValueError: if the level is not recognised.
    """
    try:
        return _VISIBILITY_NAMES[level.lower()]
    except (AttributeError, KeyError):
        raise ValueError(
            f"invalid visibility level {level!r}, expected one of "
            f"{sorted(_VISIBILITY_NAMES)}"
        ) from None


def find_serial(serials, wanted):
    """Find the index of ``wanted`` in ``serials``, comparing as strings.

    Args:
        serials (sequence): serial numbers as reported by Aravis.
        wanted: the serial being searched for; coerced to str.

    Returns:
        int or None: index of the match, or None if absent.
    """
    wanted = str(wanted)
    for index, serial in enumerate(serials):
        if str(serial) == wanted:
            return index
    return None


def apply_with_retry(setter, attributes):
    """Apply ``attributes`` via ``setter(name, value)``, retrying failures once.

    GenICam features are interdependent: setting ``Width`` before ``OffsetX``
    can fail because the existing offset plus the new width exceeds the
    sensor, yet succeed once the other features have been applied. Rather
    than maintain a dependency graph, this makes one pass in dict order,
    collects failures, then retries them once.

    Args:
        setter (callable): called as ``setter(name, value)``; raises on failure.
        attributes (dict): feature name -> value, applied in insertion order.

    Raises:
        Exception: the second failure for any feature that fails twice, with
            the first failure chained as its cause.
    """
    failures = {}
    for name, value in attributes.items():
        try:
            setter(name, value)
        except Exception as first_error:
            failures[name] = (value, first_error)

    for name, (value, first_error) in failures.items():
        try:
            setter(name, value)
        except Exception as second_error:
            raise type(second_error)(
                f"failed to set attribute {name} to {value!r} on two "
                f"attempts. First error: {first_error}. "
                f"Second error: {second_error}"
            ) from first_error
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/ -v
```

Expected: 27 passed — 5 from Task 1 (`test_labscript_devices.py`), plus 9 from
Task 2 and 13 from this task (`test_aravis_utils.py`).

If `raise type(second_error)(...)` fails because an exception class does not accept a single string argument, replace it with `raise RuntimeError(...) from first_error` and adjust `test_apply_with_retry_raises_when_failure_persists` to expect `RuntimeError`.

- [ ] **Step 5: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/aravis_utils.py AravisCamera/testing/test_aravis_utils.py
git commit -m "feat(AravisCamera): add attribute retry, serial matching, visibility helpers"
```

---

### Task 4: System prerequisites, smoke test, and README

Installs Aravis and grants the camera the OS-level access it needs. Everything before this task ran with no Aravis; everything after requires it.

**Files:**
- Create: `AravisCamera/testing/aravis_smoke_test.py`
- Create: `AravisCamera/README.md`
- Create (system, not in git): `/etc/udev/rules.d/80-aravis-flir.rules`, `/etc/tmpfiles.d/usbfs-aravis.conf`

**Interfaces:**
- Consumes: nothing
- Produces: a working `from gi.repository import Aravis` inside the labscript venv, and `aravis_smoke_test.py` as the standing diagnostic

- [ ] **Step 1: Install Aravis**

```bash
sudo apt install libaravis-0.8-0 gir1.2-aravis-0.8 aravis-tools-cli
```

- [ ] **Step 2: Symlink PyGObject into the labscript venv**

PyGObject publishes no wheels and this machine has no C toolchain, so the system package is used directly. The venv is the same CPython 3.14 as the system, so this is ABI-safe.

```bash
SP=/home/ultracold/labscript-suite/.labscript/lib/python3.14/site-packages
ln -s /usr/lib/python3/dist-packages/gi "$SP/gi"
ln -s /usr/lib/python3/dist-packages/PyGObject-3.56.2.dist-info \
      "$SP/PyGObject-3.56.2.dist-info"
```

- [ ] **Step 3: Verify Aravis imports inside the venv**

```bash
/home/ultracold/labscript-suite/.labscript/bin/python -c "
import gi
gi.require_version('Aravis', '0.8')
from gi.repository import Aravis
print('Aravis OK')
"
```

Expected: `Aravis OK`. If `ValueError: Namespace Aravis not available`, the `gir1.2-aravis-0.8` package did not install.

- [ ] **Step 4: Add the udev rule and reload**

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1e10", MODE="0660", GROUP="plugdev"' \
  | sudo tee /etc/udev/rules.d/80-aravis-flir.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Verify (replace the bus/device path with the camera's current one from `lsusb`):

```bash
lsusb | grep 1e10
ls -l /dev/bus/usb/001/*
```

Expected: the camera's node is now group `plugdev` with mode `rw-rw----`. If it is still `root root`, replug the camera.

- [ ] **Step 5: Raise the usbfs buffer limit**

`usbcore` is built into the kernel, so `modprobe.d` cannot configure it.

```bash
echo 'w /sys/module/usbcore/parameters/usbfs_memory_mb - - - - 1000' \
  | sudo tee /etc/tmpfiles.d/usbfs-aravis.conf
sudo systemd-tmpfiles --create
cat /sys/module/usbcore/parameters/usbfs_memory_mb
```

Expected: `1000`.

- [ ] **Step 6: Confirm the camera is visible to Aravis at all**

```bash
arv-tool-0.8
```

Expected: one line naming the camera, e.g. `FLIR-<model>-<serial> (USB3)`. **Record the serial number** — it is needed in Task 7 and differs from the IMAQdx-format serial in the production connection table.

If nothing is listed: the udev rule has not taken effect (replug), or the camera is not enumerated (`lsusb | grep 1e10`).

- [ ] **Step 7: Write the smoke test script**

`AravisCamera/testing/aravis_smoke_test.py`:

```python
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
```

- [ ] **Step 8: Run the smoke test against the real camera**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python AravisCamera/testing/aravis_smoke_test.py
```

Expected: device listing, model name, region, pixel format, and a decoded frame with plausible min/max. This is Tier 0 of the spec's test plan.

- [ ] **Step 9: Run the smoke test against the fake camera**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python AravisCamera/testing/aravis_smoke_test.py Fake_1
```

Expected: opens the fake device and decodes a frame. If this fails with a missing GenICam file, the fake camera's XML is not bundled in the Debian package — record that and skip fake-camera testing in later tasks, using the real camera instead.

- [ ] **Step 10: Write the README**

`AravisCamera/README.md`:

````markdown
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

## Testing

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/ -v
```

Unit tests need neither Aravis nor a camera. `testing/connection_table_aravis_test.py`
is a minimal all-dummy connection table for BLACS testing without any other
hardware connected.
````

- [ ] **Step 11: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/README.md AravisCamera/testing/aravis_smoke_test.py
git commit -m "docs(AravisCamera): add README and standalone smoke test"
```

---

### Task 5: `Aravis_Camera` — connection and attribute access

The Aravis binding's control half: open a camera by serial, read and write GenICam features.

**Files:**
- Create: `AravisCamera/blacs_workers.py`

**Interfaces:**
- Consumes: `find_serial`, `visibility_names`, `apply_with_retry` from `aravis_utils`
- Produces: `Aravis_Camera(serial_number)` with `.camera`, `.device`, `.exception_on_failed_shot`, `._abort_acquisition`, `.set_attribute(name, value)`, `.set_attributes(dict)`, `.get_attribute(name)`, `.get_attribute_names(visibility_level, writeable_only=False)`, `.close()`

- [ ] **Step 1: Introspect the installed typelib to confirm method names**

The spec specifies these from the documented C API. Confirm them against Aravis 0.8.34 before writing code:

```bash
/home/ultracold/labscript-suite/.labscript/bin/python -c "
import gi; gi.require_version('Aravis','0.8')
from gi.repository import Aravis
print('--- Device feature accessors ---')
print([m for m in dir(Aravis.Device) if 'feature' in m or 'command' in m or 'genicam' in m])
print('--- Gc node classes ---')
print([m for m in dir(Aravis) if m.startswith('Gc')])
print('--- GcVisibility members ---')
print([m for m in dir(Aravis.GcVisibility) if not m.startswith('_')])
print('--- GcCategory ---')
print([m for m in dir(Aravis.GcCategory) if not m.startswith('_')])
print('--- GcFeatureNode ---')
print([m for m in dir(Aravis.GcFeatureNode) if not m.startswith('_')])
"
```

Record the actual names. If any differ from those used below, adjust the implementation to match — the typelib is authoritative, this plan is not.

- [ ] **Step 2: Write the implementation**

`AravisCamera/blacs_workers.py`:

```python
#####################################################################
#                                                                   #
# /user_devices/AravisCamera/blacs_workers.py                       #
#                                                                   #
# Aravis (GenICam) camera worker for BLACS.                         #
#                                                                   #
#####################################################################
import sys

from labscript_devices.IMAQdxCamera.blacs_workers import IMAQdxCameraWorker

from user_devices.AravisCamera.aravis_utils import (
    apply_with_retry,
    decode_buffer,
    find_serial,
    visibility_names,
)

# Imported lazily in Aravis_Camera.__init__ so this module can be imported on
# machines without Aravis: mock mode, runmanager, and the unit tests.
Aravis = None


class Aravis_Camera(object):
    """Adapts an Aravis GenICam camera to the interface IMAQdxCameraWorker uses."""

    def __init__(self, serial_number):
        global Aravis

        try:
            import gi
        except ImportError as e:
            raise ImportError(
                "PyGObject ('gi') is not available in this environment. It is "
                "not pip-installable here; symlink the system package into the "
                "venv as described in AravisCamera/README.md, section 2."
            ) from e

        try:
            gi.require_version('Aravis', '0.8')
            from gi.repository import Aravis as _Aravis
        except (ImportError, ValueError) as e:
            raise ImportError(
                "The Aravis GObject-introspection typelib is not installed. "
                "Run: sudo apt install libaravis-0.8-0 gir1.2-aravis-0.8 "
                "aravis-tools-cli  (see AravisCamera/README.md, section 1)."
            ) from e

        Aravis = _Aravis

        self.serial_number = str(serial_number)
        self.exception_on_failed_shot = True
        self._abort_acquisition = False
        self.stream = None

        if self.serial_number.startswith('Fake'):
            # Aravis' built-in fake GenICam device, addressed by id not serial.
            Aravis.enable_interface('Fake')
            self.camera = Aravis.Camera.new(self.serial_number)
        else:
            print("Finding camera...")
            Aravis.update_device_list()
            serials = [
                Aravis.get_device_serial_nbr(i)
                for i in range(Aravis.get_n_devices())
            ]
            index = find_serial(serials, self.serial_number)
            if index is None:
                raise ValueError(
                    f"No connected camera with serial number "
                    f"{self.serial_number!r}. Found: {serials}. "
                    f"Note that Aravis reports the GenICam DeviceSerialNumber, "
                    f"which differs from an IMAQdx-format serial. "
                    f"Run 'arv-tool-0.8' to list cameras."
                )
            print("Connecting to camera...")
            self.camera = Aravis.Camera.new(Aravis.get_device_id(index))

        self.device = self.camera.get_device()

    # ---------------------------------------------------------------- attributes

    def _feature_node(self, name):
        node = self.device.get_feature(name)
        if node is None:
            raise ValueError(
                f"camera has no GenICam feature named {name!r}. Run "
                f"'arv-tool-0.8 features' to list available features. Note "
                f"that names are bare, not IMAQdx-style 'Category::Feature'."
            )
        return node

    def get_attribute(self, name):
        """Return the current value of the named GenICam feature."""
        node = self._feature_node(name)
        try:
            # Enumeration and String are checked first: a GcEnumeration also
            # satisfies the integer interface, and we want its symbolic name.
            if isinstance(node, (Aravis.GcEnumeration, Aravis.GcString)):
                return self.device.get_string_feature_value(name)
            if isinstance(node, Aravis.GcBoolean):
                return self.device.get_boolean_feature_value(name)
            if isinstance(node, Aravis.GcInteger):
                return self.device.get_integer_feature_value(name)
            if isinstance(node, Aravis.GcFloat):
                return self.device.get_float_feature_value(name)
        except Exception as e:
            raise Exception(f"Failed to get attribute {name}") from e
        raise TypeError(
            f"cannot read GenICam feature {name!r} of type "
            f"{type(node).__name__}"
        )

    def set_attribute(self, name, value):
        """Set the named GenICam feature to the given value."""
        node = self._feature_node(name)
        try:
            if isinstance(node, Aravis.GcCommand):
                self.device.execute_command(name)
            elif isinstance(node, (Aravis.GcEnumeration, Aravis.GcString)):
                self.device.set_string_feature_value(name, str(value))
            elif isinstance(node, Aravis.GcBoolean):
                self.device.set_boolean_feature_value(name, bool(value))
            elif isinstance(node, Aravis.GcInteger):
                self.device.set_integer_feature_value(name, int(value))
            elif isinstance(node, Aravis.GcFloat):
                self.device.set_float_feature_value(name, float(value))
            else:
                raise TypeError(
                    f"cannot write GenICam feature {name!r} of type "
                    f"{type(node).__name__}"
                )
        except Exception as e:
            raise Exception(f"failed to set attribute {name} to {value}") from e

    def set_attributes(self, attr_dict):
        """Set many features, retrying failures once (see apply_with_retry)."""
        apply_with_retry(self.set_attribute, attr_dict)

    def get_attribute_names(self, visibility_level, writeable_only=False):
        """List readable feature names at or below the given visibility level."""
        wanted = {
            getattr(Aravis.GcVisibility, name)
            for name in visibility_names(visibility_level)
        }
        genicam = self.device.get_genicam()
        names = []
        seen = set()

        def walk(category_node):
            for feature_name in Aravis.GcCategory.get_features(category_node):
                if feature_name in seen:
                    continue
                seen.add(feature_name)
                node = genicam.get_node(feature_name)
                if node is None:
                    continue
                if isinstance(node, Aravis.GcCategory):
                    walk(node)
                    continue
                if isinstance(node, Aravis.GcCommand):
                    # Reading a command would execute it.
                    continue
                try:
                    if not Aravis.GcFeatureNode.is_available(node):
                        continue
                    if not Aravis.GcFeatureNode.is_implemented(node):
                        continue
                    if node.get_visibility() not in wanted:
                        continue
                except Exception:
                    continue
                names.append(feature_name)

        root = genicam.get_node('Root')
        if root is not None:
            walk(root)
        return names

    # ---------------------------------------------------------------- lifecycle

    def close(self):
        self.stream = None
        self.device = None
        self.camera = None
```

- [ ] **Step 3: Verify connection and attribute access against the fake camera**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -c "
from user_devices.AravisCamera.blacs_workers import Aravis_Camera
cam = Aravis_Camera('Fake_1')
names = cam.get_attribute_names('intermediate')
print(f'{len(names)} features at intermediate visibility')
print('sample:', names[:10])
print('PixelFormat =', cam.get_attribute('PixelFormat'))
cam.set_attributes({'PixelFormat': 'Mono8'})
print('after set  =', cam.get_attribute('PixelFormat'))
cam.close()
print('OK')
"
```

Expected: a non-empty feature list and a `PixelFormat` that reads back as `Mono8`.

- [ ] **Step 4: Verify the serial-not-found error is informative**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -c "
from user_devices.AravisCamera.blacs_workers import Aravis_Camera
try:
    Aravis_Camera('definitely-not-a-serial')
except ValueError as e:
    print(e)
"
```

Expected: message lists the serials actually found, so the real serial can be read straight out of it.

- [ ] **Step 5: Verify against the real camera**

This reads the serial from Aravis rather than requiring it to be transcribed:

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -c "
import gi; gi.require_version('Aravis','0.8')
from gi.repository import Aravis
from user_devices.AravisCamera.blacs_workers import Aravis_Camera

Aravis.update_device_list()
assert Aravis.get_n_devices() > 0, 'no camera connected'
serial = Aravis.get_device_serial_nbr(0)
print('Using serial:', serial)

cam = Aravis_Camera(serial)
counts = {lvl: len(cam.get_attribute_names(lvl))
          for lvl in ('simple', 'intermediate', 'advanced')}
print(counts)
print('ExposureTime:', cam.get_attribute('ExposureTime'))
cam.close()
assert counts['simple'] <= counts['intermediate'] <= counts['advanced']
assert counts['advanced'] > 0
print('OK')
"
```

Expected: feature counts that do not decrease as visibility widens, and
`ExposureTime` returning a float. Note the serial printed here is the one to
put in the connection table in Tasks 7–8.

- [ ] **Step 6: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/blacs_workers.py
git commit -m "feat(AravisCamera): add Aravis_Camera connection and attribute access"
```

---

### Task 6: `Aravis_Camera` — acquisition and streaming

The acquisition half: stream setup, buffer recycling, and the abort-aware grab loop.

**Files:**
- Modify: `AravisCamera/blacs_workers.py` (add methods to `Aravis_Camera`)

**Interfaces:**
- Consumes: `Aravis_Camera` from Task 5, `decode_buffer` from `aravis_utils`
- Produces: `.configure_acquisition(continuous=True, bufferCount=5)`, `.snap()`, `.grab()`, `.grab_multiple(n_images, images, waitForNextBuffer=True)`, `.stop_acquisition()`, `.abort_acquisition()` — all returning/appending `numpy.ndarray` of shape `(height, width)`

- [ ] **Step 1: Add the acquisition methods**

Insert into `Aravis_Camera` in `AravisCamera/blacs_workers.py`, between the attribute section and `close()`:

```python
    # ---------------------------------------------------------------- acquisition

    #: How long to wait for a buffer before checking the abort flag, in
    #: microseconds. Short enough that BLACS' abort button stays responsive
    #: while the camera waits for a hardware trigger.
    POLL_TIMEOUT_US = 200_000

    def _buffer_to_array(self, buffer):
        return decode_buffer(
            buffer.get_data(),
            buffer.get_image_width(),
            buffer.get_image_height(),
        )

    def configure_acquisition(self, continuous=True, bufferCount=5):
        """Create the stream, allocate buffers, and start acquisition.

        Note this *starts* acquisition, matching IMAQdxCamera semantics that
        transition_to_buffered relies on. Both continuous and buffered use
        GenICam AcquisitionMode 'Continuous'; ``continuous`` only reflects the
        caller's intent and is accepted for signature compatibility.
        """
        self._abort_acquisition = False
        self.camera.set_acquisition_mode(Aravis.AcquisitionMode.CONTINUOUS)
        payload = self.camera.get_payload()
        self.stream = self.camera.create_stream(None, None)
        if self.stream is None:
            raise RuntimeError("failed to create Aravis stream")
        for _ in range(bufferCount):
            self.stream.push_buffer(Aravis.Buffer.new_allocate(payload))
        self.camera.start_acquisition()

    def snap(self):
        """Acquire a single image and return it."""
        buffer = self.camera.acquisition(0)
        if buffer is None:
            raise RuntimeError("failed to acquire an image")
        return self._buffer_to_array(buffer)

    def grab(self, waitForNextBuffer=True):
        """Grab one image from a stream already started by configure_acquisition."""
        buffer = self.stream.timeout_pop_buffer(self.POLL_TIMEOUT_US)
        if buffer is None:
            raise TimeoutError("timed out waiting for a frame")
        try:
            status = buffer.get_status()
            if status != Aravis.BufferStatus.SUCCESS:
                raise RuntimeError(f"buffer received with status {status}")
            return self._buffer_to_array(buffer)
        finally:
            self.stream.push_buffer(buffer)

    def grab_multiple(self, n_images, images, waitForNextBuffer=True):
        """Grab n_images frames, appending each to the images list.

        Polls with a timeout rather than blocking so that _abort_acquisition
        is checked regularly: this is what keeps BLACS' abort responsive while
        the camera is armed and waiting for triggers.
        """
        print(f"Attempting to grab {n_images} images.")
        for i in range(n_images):
            while True:
                if self._abort_acquisition:
                    print("Abort during acquisition.")
                    self._abort_acquisition = False
                    return
                buffer = self.stream.timeout_pop_buffer(self.POLL_TIMEOUT_US)
                if buffer is None:
                    print('.', end='')
                    sys.stdout.flush()
                    continue
                try:
                    status = buffer.get_status()
                    if status != Aravis.BufferStatus.SUCCESS:
                        msg = (
                            f"image {i + 1} of {n_images} received with "
                            f"buffer status {status}"
                        )
                        if self.exception_on_failed_shot:
                            raise RuntimeError(msg)
                        print(msg, file=sys.stderr)
                        break
                    images.append(self._buffer_to_array(buffer))
                finally:
                    self.stream.push_buffer(buffer)
                print(f"Got image {i + 1} of {n_images}.")
                break
        print(f"Got {len(images)} of {n_images} images.")

    def stop_acquisition(self):
        self.camera.stop_acquisition()
        # Dropping the stream reference frees its buffers.
        self.stream = None

    def abort_acquisition(self):
        self._abort_acquisition = True
```

- [ ] **Step 2: Verify streaming against the fake camera**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -c "
from user_devices.AravisCamera.blacs_workers import Aravis_Camera
cam = Aravis_Camera('Fake_1')
cam.configure_acquisition(continuous=False, bufferCount=5)
images = []
cam.grab_multiple(3, images)
cam.stop_acquisition()
print('got', len(images), 'images')
for im in images:
    print('  ', im.shape, im.dtype, im.flags['OWNDATA'])
assert len(images) == 3
assert all(im.flags['OWNDATA'] for im in images)
cam.close()
print('OK')
"
```

Expected: 3 images, each owning its data.

- [ ] **Step 3: Verify buffers are genuinely recycled**

Grabbing more images than there are buffers proves `push_buffer` is returning them to the pool. Without recycling this hangs.

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
timeout 60 ../../.labscript/bin/python -c "
from user_devices.AravisCamera.blacs_workers import Aravis_Camera
cam = Aravis_Camera('Fake_1')
cam.configure_acquisition(continuous=False, bufferCount=3)
images = []
cam.grab_multiple(12, images)
cam.stop_acquisition(); cam.close()
print('got', len(images), 'images from a 3-buffer pool')
assert len(images) == 12
print('OK')
"
```

Expected: 12 images. A timeout means buffers are not being pushed back.

- [ ] **Step 4: Verify abort works**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
timeout 60 ../../.labscript/bin/python -c "
import threading, time
from user_devices.AravisCamera.blacs_workers import Aravis_Camera
cam = Aravis_Camera('Fake_1')
cam.configure_acquisition(continuous=False, bufferCount=5)
images = []
t = threading.Thread(target=cam.grab_multiple, args=(10_000, images), daemon=True)
t.start()
time.sleep(2)
cam.abort_acquisition()
t.join(timeout=10)
assert not t.is_alive(), 'grab_multiple did not stop on abort'
cam.stop_acquisition(); cam.close()
print('aborted cleanly after', len(images), 'images')
print('OK')
"
```

Expected: the thread exits promptly and reports however many images it managed.

- [ ] **Step 5: Verify snap and streaming against the real camera**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -c "
import gi; gi.require_version('Aravis','0.8')
from gi.repository import Aravis
from user_devices.AravisCamera.blacs_workers import Aravis_Camera

Aravis.update_device_list()
assert Aravis.get_n_devices() > 0, 'no camera connected'
cam = Aravis_Camera(Aravis.get_device_serial_nbr(0))
cam.set_attributes({'TriggerMode': 'Off'})
im = cam.snap()
print('snap:', im.shape, im.dtype, 'min', im.min(), 'max', im.max())
cam.configure_acquisition(continuous=False, bufferCount=5)
images = []
cam.grab_multiple(5, images)
cam.stop_acquisition(); cam.close()
print('grabbed', len(images), 'free-running frames')
assert len(images) == 5
print('OK')
"
```

Expected: a real frame with a non-trivial min/max, then 5 free-running frames.

- [ ] **Step 6: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/blacs_workers.py
git commit -m "feat(AravisCamera): add acquisition, streaming and abort handling"
```

---

### Task 7: BLACS integration — worker, tab, registration, test connection table

Wires the binding into BLACS and proves a full shot end to end.

**Files:**
- Modify: `AravisCamera/blacs_workers.py` (append `AravisCameraWorker`)
- Create: `AravisCamera/blacs_tabs.py`
- Create: `AravisCamera/register_classes.py`
- Create: `AravisCamera/testing/connection_table_aravis_test.py`

**Interfaces:**
- Consumes: `Aravis_Camera` from Tasks 5–6, `AravisCamera` from Task 1
- Produces: `AravisCameraWorker` (used by BLACS via the tab), `AravisCameraTab` (referenced by `register_classes`)

- [ ] **Step 1: Append the worker class**

At the end of `AravisCamera/blacs_workers.py`:

```python
class AravisCameraWorker(IMAQdxCameraWorker):
    """BLACS worker for Aravis cameras.

    All buffered-shot logic, HDF5 output and image transport are inherited;
    only the camera interface class differs.
    """

    interface_class = Aravis_Camera
```

- [ ] **Step 2: Write the BLACS tab**

`AravisCamera/blacs_tabs.py`:

```python
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
```

- [ ] **Step 3: Write the registration module**

`AravisCamera/register_classes.py`:

```python
#####################################################################
#                                                                   #
# /user_devices/AravisCamera/register_classes.py                    #
#                                                                   #
#####################################################################
from labscript_devices import register_classes

register_classes(
    'AravisCamera',
    BLACS_tab='user_devices.AravisCamera.blacs_tabs.AravisCameraTab',
    runviewer_parser=None,
)
```

- [ ] **Step 4: Write the test connection table**

`AravisCamera/testing/connection_table_aravis_test.py`:

```python
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
```

- [ ] **Step 5: Verify the table compiles in mock mode**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
sed -i 's/^MOCK = False/MOCK = True/' AravisCamera/testing/connection_table_aravis_test.py
../../.labscript/bin/python AravisCamera/testing/connection_table_aravis_test.py
sed -i 's/^MOCK = True/MOCK = False/' AravisCamera/testing/connection_table_aravis_test.py
```

Expected: compiles without error. This proves registration, the device class, and `expose()` all work with no hardware.

- [ ] **Step 6: Run a full shot in BLACS against the fake camera**

Point labconfig at the test table:

```bash
cd /home/ultracold/labscript-suite
cp labconfig/ultracold-HP-EliteDesk-800-G6-Tower-PC.ini \
   labconfig/ultracold-HP-EliteDesk-800-G6-Tower-PC.ini.bak
```

Edit `connection_table_py` under `[paths]` to point at
`userlib/user_devices/AravisCamera/testing/connection_table_aravis_test.py`,
then start BLACS and runmanager, compile the connection table, and run one shot.

Verify in BLACS:
- The `test_cam` tab appears and reaches a normal (non-error) state.
- Manual mode "Snap" produces an image in the tab.
- A shot completes and reports 2 of 2 images.

- [ ] **Step 7: Verify the shot file contents**

```bash
cd /home/ultracold/labscript-suite
.labscript/bin/python -c "
import labscript_utils.h5_lock, h5py, sys
path = sys.argv[1]
with h5py.File(path, 'r') as f:
    g = f['images/test']
    print('camera attr:', g.attrs['camera'])
    print('failed_shot:', g.attrs['failed_shot'])
    for name in g:
        for frametype in g[name]:
            d = g[name][frametype]
            print(f'  {name}/{frametype}: shape={d.shape} dtype={d.dtype}')
" <path to the shot h5 file>
```

Expected: `failed_shot` is `False`, and `first/frame` and `second/frame` datasets exist with plausible shapes and dtype `uint16`.

- [ ] **Step 8: Restore the labconfig**

```bash
cd /home/ultracold/labscript-suite
mv labconfig/ultracold-HP-EliteDesk-800-G6-Tower-PC.ini.bak \
   labconfig/ultracold-HP-EliteDesk-800-G6-Tower-PC.ini
```

- [ ] **Step 9: Run the full unit test suite**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
../../.labscript/bin/python -m pytest AravisCamera/testing/ -v
```

Expected: 27 passed. No test should have started requiring Aravis or a camera —
the unit suite must stay runnable on a machine with neither.

- [ ] **Step 10: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/blacs_workers.py AravisCamera/blacs_tabs.py \
        AravisCamera/register_classes.py \
        AravisCamera/testing/connection_table_aravis_test.py
git commit -m "feat(AravisCamera): add BLACS worker, tab, registration and test table"
```

---

### Task 8: Real-camera validation

Repeats Task 7's BLACS run against the physical camera. Tiers 3 and 4 of the spec's test plan.

**Files:**
- Modify: `AravisCamera/testing/connection_table_aravis_test.py` (serial only)
- Modify: `AravisCamera/README.md` (record validated status)

**Interfaces:**
- Consumes: everything from Tasks 1–7
- Produces: no new code — a validation record

- [ ] **Step 1: Point the test table at the real camera**

Edit `SERIAL` in `AravisCamera/testing/connection_table_aravis_test.py` to the
serial recorded in Task 4 Step 6.

- [ ] **Step 2: Move the camera to a USB3 port**

The camera was found on a 480 Mbps bus. Move it to a USB3 port and confirm:

```bash
lsusb -t | grep -A1 -B1 5000M
lsusb | grep 1e10
```

Expected: the camera appears under a 5000M bus.

- [ ] **Step 3: Run a BLACS shot against the real camera**

Repeat Task 7 Steps 6–8 with the real serial. Verify:
- The tab connects and reports the real camera model.
- Manual-mode Snap shows a live image that changes when the lens cap moves.
- A two-exposure shot completes with `failed_shot = False`.

This is Tier 4: the whole buffered path with the camera free-running.

- [ ] **Step 4: Verify manual-mode continuous acquisition**

In BLACS, start continuous acquisition on the `test_cam` tab and confirm frames
update steadily, then stop it. This exercises `grab()` and the worker's
continuous thread, which the scripted tests do not cover.

- [ ] **Step 5: Record what was and was not validated**

Append to `AravisCamera/README.md`:

```markdown
## Validation status

Validated on Ubuntu 26.04, Python 3.14.4, Aravis 0.8.34, against a FLIR
USB3 Vision camera (vendor 1e10):

- Enumeration by serial number, and the error listing available serials
- GenICam feature read/write across all three visibility levels
- Manual-mode snap and continuous acquisition
- Free-running buffered shots end to end, including HDF5 output

**Not validated: hardware-triggered acquisition.** The test camera has no
trigger line wired to a pseudoclocked digital output. The trigger code path
is inherited unchanged from `IMAQdxCamera` and the camera-side configuration
is set through `camera_attributes`, but neither has been exercised. Before
relying on triggered shots, verify:

1. `arv-tool-0.8 features` shows the expected `TriggerSource` (FLIR USB3
   models use `Line0` for the opto-isolated input).
2. A shot with `TriggerMode='On'` acquires exactly as many frames as there
   were `expose()` calls, rather than timing out.
```

- [ ] **Step 6: Commit**

```bash
cd /home/ultracold/labscript-suite/userlib/user_devices
git add AravisCamera/README.md AravisCamera/testing/connection_table_aravis_test.py
git commit -m "docs(AravisCamera): record real-camera validation status"
```

---

## Appendix: verified facts this plan relies on

Established by running code against this machine on 2026-08-25. If any turn
out false during execution, stop and re-check rather than working around them.

| Fact | Evidence |
|---|---|
| `IMAQdxCamera` hex-parses string serials | `serial_number='15551991'` produced `cam.serial_number == 357898641` |
| The hex-parsed value is what reaches the worker | `connection_table_properties['serial_number']` was the int `357898641` |
| The Task 1 override fixes it | The same probe with the override saved the string `'15551991'` |
| Headless labscript compilation works | `labscript_init` + `start()`/`expose()`/`stop()` compiled with no display |
| `labscript_utils.h5_lock` must precede `h5py` | Importing h5py first raised `ImportError: h5_lock must be imported before any code imports h5py` |
| `blacs.tab_base_classes` imports headless | `from blacs.tab_base_classes import Worker` succeeded with no display |
| `user_devices` is an importable namespace package | `user_devices.__path__` resolved to `userlib/user_devices` |
| pytest installs without a compiler | `pip download pytest --only-binary=:all:` fetched pure wheels |
| PyGObject cannot be pip-installed here | No wheels exist for any release; no `gcc`/`meson`/`pkg-config` on this machine |
| Aravis 0.8.34 is apt-installable | `libaravis-0.8-0`, `gir1.2-aravis-0.8`, `aravis-tools-cli` in Ubuntu universe |
| System `gi` imports under the venv interpreter | Verified with `PYTHONPATH=/usr/lib/python3/dist-packages` |
| The camera is USB3 Vision | USB `1e10:4000`, product string `U3V camera` |
| `usbcore` is built in, not a module | Present in `/lib/modules/$(uname -r)/modules.builtin` |
| The Aravis streaming API is as used here | Upstream `tests/fake.py` uses `create_stream`, `Buffer.new_allocate`, `push_buffer`, `pop_buffer`, `get_status`, `get_data`, `acquisition()` |
