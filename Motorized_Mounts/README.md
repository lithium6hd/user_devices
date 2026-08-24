# Motorized Mirror Mounts — labscript device

labscript-suite integration for the Arduino-driven motorized mirror mounts
(firmware: `Motorized-mirror-mounts/Arduino/servocontrol_v1_87`).

Each mirror is steered by **two servos** (one for the *x* tilt, one for the *y*
tilt). A single Arduino (Ethernet, TCP port 5000) drives up to 16 servos. This
device lets you set servo positions from the BLACS front panel and from
experiment scripts, in any of the firmware's **move modes** (default:
precision `P`).

## Move modes

| Mode | Firmware command | Behaviour |
|------|------------------|-----------|
| `'P'` precision (default) | `P<n>:<pos>;<a>,<b>` | power on → move → wait `a + b·|Δ|` ms → power off |
| `'A'` auto | `A<n>:<pos>` | power on → move → wait `200 + 1.5·|Δ|` ms → power off |
| `'S'` set | `S<n>:<pos>` | move only; servo power is left unchanged (use the ON/OFF buttons) |

The default mode is set per controller with `default_mode=`. In an experiment
script, override it per move with `mode=`. In the BLACS front panel, pick the
mode from the **Move mode** selector (it applies to all manual moves); the
**Servos ON / OFF** buttons control servo power (needed with mode `S`).

> If a `P` move returns `E00` ("unknown command") in the BLACS log, the deployed
> firmware does not implement precision mode — set `default_mode='A'` (or `'S'`)
> or pick another mode in the panel.

## Files

| File | Role |
|------|------|
| `labscript_devices.py` | `MotorizedMirrorMounts` controller, `MirrorServo` output, `Mirror` grouping helper |
| `blacs_tabs.py` | BLACS front panel (one bounded spinbox per servo, grouped by mirror) |
| `blacs_workers.py` | TCP client + position tracking / smart move logic |
| `register_classes.py` | registers the device with BLACS |
| `utils.py` | shared constants and the `servo N` connection parser |

## Positions

* Position setpoints are integers in **`[0, 205]`** (the firmware's range).
* Set **per-servo bounds** with `limits=(min, max)` (or `x_limits`/`y_limits`
  on a `Mirror`). Bounds apply both in the GUI and in scripts.

## Connection table

```python
from user_devices.Motorized_Mounts.labscript_devices import (
    MotorizedMirrorMounts, Mirror, MirrorServo,
)

mounts = MotorizedMirrorMounts(
    name='mirror_mounts',
    host='10.11.1.87',    # Arduino IP address
    port=5000,
    default_mode='P',     # 'P' precision, 'A' auto, or 'S' set
    # Precision-mode settle time = precision_a + precision_b * |delta| (ms):
    precision_a=200,
    precision_b=2,
    # mock=True,           # simulate the hardware (no TCP) for testing
)

# A mirror = two servos (x on channel 0, y on channel 1), each bounded:
mot_mirror = Mirror(
    name='mot_mirror',
    parent_device=mounts,
    x_channel=0, y_channel=1,
    x_limits=(20, 180),
    y_limits=(30, 170),
)

# You can also add a lone servo directly:
extra = MirrorServo('extra_servo', mounts, connection=4, limits=(0, 205))
```

## Experiment script

```python
# Command both axes of a mirror (default mode):
mot_mirror.move(x=120, y=95)

# ...choose a mode for this move:
mot_mirror.move(x=120, y=95, mode='A')     # auto mode
mot_mirror.x.move(120, mode='S')           # set mode, x only

# ...or use the plain static-output API (uses the default mode):
mot_mirror.y.constant(95)
extra.constant(100)
```

An axis you don't command in a shot **keeps its previous position** — it is not
moved to a default.

## Behaviour (how the requirements are met)

* **Precision mode `P` is the default move**, with `A` and `S` selectable per
  move (script) or from the panel. See *Move modes* above.
* **Get on start-up only / when unclear.** At BLACS start-up the tab calls
  `check_remote_values`, which issues a `Get<n>` for each servo (all positions
  start "unclear") and shows them in the panel. After that, positions are
  tracked in software; `Get` is re-issued only for a servo whose position has
  become unclear (e.g. after a comms error), never on every shot. The
  **"Query current positions"** button forces a fresh read of all servos.
* **Move only if changed.** A servo is moved only when the commanded position
  differs from its known position. After a successful move the software assumes
  the servo reached the target (as instructed) and updates its cache — no
  confirmation `Get`.
* **Bounds per servo** via `limits` (see above).
* **Two servos per mirror** via `Mirror`, exposing `.x` and `.y`.

## Notes on the firmware

The `Get<n>` command returns the *last commanded* position (0–205). Immediately
after the Arduino powers up — before any servo has been commanded — it returns
`410`, which the worker treats as "position unclear" and resolves by commanding
the first requested position.
