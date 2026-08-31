"""Timing tests for the DIO32-TiCo digital output table.

The TiCo consumes ``DIGITAL_OUT/<card>`` as a list of (n_cycles, bitfield)
events, one event per TiCo cycle. Two events that land on the same n_cycles
therefore cannot both be applied, so the table must be strictly increasing in
time, and each event must sit on the cycle labscript actually asked for.
"""
import labscript_utils.h5_lock  # noqa: F401  (must precede h5py)

import h5py
import numpy as np
import pytest

from user_devices.ADwinProII import PROCESSDELAY_TiCo, CLOCK_TiCo
from user_devices.ADwinProII.labscript_devices import ADwinProII
from user_devices.ADwinProII.labscript_devices_ADwin_modules import (
    ADwinAI8,
    ADwinAO8,
    ADwinAnalogOut,
    ADwinDIO32,
)

us = 1e-6
ms = 1e-3

# One TiCo cycle. This is also the smallest event spacing that
# _ADwin_CPU_TiCo.collect_change_times accepts, so events this far apart must
# end up on adjacent cycles rather than colliding.
TICO_RESOLUTION = PROCESSDELAY_TiCo / CLOCK_TiCo


@pytest.fixture
def dio32(labscript_shot):
    """An ADwinProII with a single DIO32 card on its own TiCo.

    ADwinProII.collect_card_instructions concatenates the analog tables
    unconditionally, so an AO8 and an AI8 have to be present even though these
    tests only look at the digital table.
    """
    import labscript

    adwin = ADwinProII("adwin", 1, "buffered", "manual")
    card = ADwinDIO32("DIO32_2", adwin, module_address=5, TiCo=True)
    ao8 = ADwinAO8("AO8_1", adwin, module_address=3)
    ADwinAI8("AI8_1", adwin, module_address=2)
    ADwinAnalogOut("analog", parent_device=ao8, connection="1")
    labscript.DigitalOut("aom", parent_device=card, connection="1")
    labscript.Shutter(
        "shutter", parent_device=card, connection="2", delay=(0, 0), open_state=1
    )
    return card


def _digital_data(h5_path, card_name="DIO32_2"):
    with h5py.File(h5_path, "r") as f:
        return f[f"devices/adwin/DIGITAL_OUT/{card_name}"][:]


def test_events_one_cycle_apart_do_not_collide(labscript_shot, dio32):
    """Events a single TiCo cycle apart must land on separate cycles.

    This is the flashing-pulse pattern from the HQA sequence: the AOM is
    switched a cycle either side of the pulse while the shutter closes on the
    pulse edge itself. ``t`` is deliberately a time whose float64
    representation falls just short of an exact cycle boundary
    (1.040701 / 0.5us == 2081401.9999999998), which is where truncating
    instead of rounding drops the event onto the previous cycle.
    """
    import labscript

    labscript.start()
    t = 1.040701
    shutter.open(t - 5.5 * ms)
    aom.go_high(t - TICO_RESOLUTION)
    aom.go_low(t + 20 * us)
    shutter.close(t)
    labscript.stop(t + 100 * ms)

    n_cycles = _digital_data(labscript_shot)["n_cycles"]
    assert np.all(np.diff(n_cycles) > 0), (
        "digital output events collided on the same TiCo cycle:\n"
        f"{n_cycles}"
    )


def test_n_cycles_matches_requested_times(labscript_shot, dio32):
    """Each event must sit on the cycle nearest its requested time."""
    import labscript

    labscript.start()
    t = 1.040701
    aom.go_high(t)
    aom.go_low(t + 20 * us)
    labscript.stop(t + 100 * ms)

    data = _digital_data(labscript_shot)
    n_cycles = data["n_cycles"]
    for requested in (t, t + 20 * us):
        expected = round(requested / TICO_RESOLUTION)
        assert expected in n_cycles, (
            f"no event at cycle {expected} (t={requested}s); table holds "
            f"{n_cycles}"
        )
