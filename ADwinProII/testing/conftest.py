"""pytest fixtures for ADwinProII tests.

labscript_utils.h5_lock MUST be imported before h5py anywhere in the process,
so it is imported here first, before anything else pulls h5py in. Matches
user_devices/AravisCamera/testing/conftest.py.
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
