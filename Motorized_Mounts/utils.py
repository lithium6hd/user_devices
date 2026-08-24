#####################################################################
#                                                                   #
# user_devices/Motorized_Mounts/utils.py                            #
#                                                                   #
# Shared constants and helpers for the motorized mirror-mount       #
# servo controller (Arduino firmware servocontrol_v1_87).           #
#                                                                   #
#####################################################################
"""Constants and small helpers shared by the labscript device, BLACS tab and
worker for the motorized mirror mounts.

The hardware is an Arduino (Ethernet, TCP port 5000) driving up to 16 hobby
servos via an Adafruit PWM driver.  Each servo accepts an integer *position*
setpoint in the range ``[POSITION_MIN, POSITION_MAX]``.  See the firmware in
``Motorized-mirror-mounts/Arduino/servocontrol_v1_87`` for the wire protocol.
"""

# --- Hardware limits (from the Arduino firmware) --------------------------

#: Lowest position setpoint the firmware accepts (``constrain(pos, 0, 205)``).
POSITION_MIN = 0
#: Highest position setpoint the firmware accepts (``constrain(pos, 0, 205)``).
POSITION_MAX = 205
#: Number of servo channels the PWM driver exposes (indices 0..15).
NUM_SERVOS = 16

#: Sentinel written to the shot file for a servo that is *not* commanded in a
#: shot.  The worker treats this as "hold the current position" and never moves
#: the servo.  It is deliberately outside ``[POSITION_MIN, POSITION_MAX]`` so it
#: can never collide with a real setpoint.
HOLD = -1

# --- Precision-mode ('P') timing parameters -------------------------------
# The firmware 'P' command switches the servo power on, drives to the target,
# waits ``t_wait = a + b * |delta_position|`` milliseconds for the move to
# settle, then switches the power off again.  These are the defaults; they can
# be overridden per controller in the connection table.
DEFAULT_PRECISION_A = 200  # constant term of the settle time, in ms
DEFAULT_PRECISION_B = 2    # per-step term of the settle time, in ms/step

# --- Move modes -----------------------------------------------------------
# The firmware exposes three ways to command a servo position:
#   'P'  precision : P<n>:<pos>;<a>,<b>  -> power on, move, wait a+b*|d|, power off
#   'A'  auto      : A<n>:<pos>          -> power on, move, wait 200+1.5*|d|, power off
#   'S'  set       : S<n>:<pos>          -> move only; servo power is left as-is
# 'P' and 'A' manage servo power automatically; 'S' does not (use the on/off
# power commands with it).
MODE_PRECISION = 'P'
MODE_AUTO = 'A'
MODE_SET = 'S'
MODES = (MODE_PRECISION, MODE_AUTO, MODE_SET)

#: Human-readable labels for the front-panel mode selector.
MODE_LABELS = {
    MODE_PRECISION: 'Precision (P) - auto power, tunable settle',
    MODE_AUTO:      'Auto (A) - auto power, fixed settle',
    MODE_SET:       'Set (S) - move only, no power change',
}

#: Default move mode if none is specified.
DEFAULT_MODE = MODE_PRECISION

# Accepted aliases (case-insensitive) for :func:`normalise_mode`.
_MODE_ALIASES = {
    'p': MODE_PRECISION, 'precision': MODE_PRECISION,
    'a': MODE_AUTO, 'auto': MODE_AUTO,
    's': MODE_SET, 'set': MODE_SET,
}


def normalise_mode(mode):
    """Return the canonical single-letter move mode ('P', 'A' or 'S').

    Accepts the letter or the full name, case-insensitively.  Raises
    ``ValueError`` for anything else.
    """
    if mode is None:
        return DEFAULT_MODE
    try:
        return _MODE_ALIASES[str(mode).strip().lower()]
    except KeyError:
        raise ValueError(
            f"Invalid move mode {mode!r}; expected one of "
            f"{sorted(set(_MODE_ALIASES))}"
        ) from None


def get_servo_number(connection):
    """Return the integer servo index (0..15) for a servo *connection*.

    Accepts either an ``int`` or a string of the form ``"servo <n>"`` (the
    canonical connection string used in the connection table).  Raises
    ``ValueError`` if the value cannot be parsed or is out of range.
    """
    if isinstance(connection, bool):
        # bool is a subclass of int; reject it explicitly to avoid surprises.
        raise ValueError(f"Invalid servo connection {connection!r}")
    if isinstance(connection, int):
        num = connection
    else:
        text = str(connection).strip().lower()
        if text.startswith("servo"):
            text = text[len("servo"):].strip()
        try:
            num = int(text)
        except ValueError:
            raise ValueError(
                f"Invalid servo connection {connection!r}; "
                f"expected 'servo <n>' or an int in 0..{NUM_SERVOS - 1}"
            ) from None
    if not (0 <= num <= NUM_SERVOS - 1):
        raise ValueError(
            f"Servo number {num} out of range 0..{NUM_SERVOS - 1}"
        )
    return num


def servo_connection(channel):
    """Return the canonical connection string ``"servo <n>"`` for *channel*.

    *channel* may be an int or an existing connection string; it is validated
    against the allowed servo range.
    """
    return f"servo {get_servo_number(channel)}"
