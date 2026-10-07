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
