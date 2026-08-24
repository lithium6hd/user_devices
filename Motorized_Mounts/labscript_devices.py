#####################################################################
#                                                                   #
# user_devices/Motorized_Mounts/labscript_devices.py                #
#                                                                   #
# labscript device for the motorized mirror mounts.                 #
#                                                                   #
#####################################################################
"""labscript device for the Arduino-driven motorized mirror mounts.

The physical setup is a set of kinematic mirror mounts, each steered by *two*
hobby servos (one for the horizontal / *x* tilt, one for the vertical / *y*
tilt).  All servos of one mount cluster are driven by a single Arduino
(Ethernet, TCP port 5000) running the ``servocontrol_v1_87`` firmware.

Design notes
------------
* Each servo is modelled as a :class:`MirrorServo`, a *static* analog output
  whose value is an integer position setpoint in ``[0, 205]``.  Per-servo
  ``limits`` restrict the reachable range (both in the BLACS front panel and in
  experiment scripts).
* The two servos of one mirror are conveniently grouped with :class:`Mirror`.
* The Arduino board itself is a :class:`MotorizedMirrorMounts` controller.  It
  is a *parentless* (top-level) device: it is not clocked by a pseudoclock, it
  is simply told a static position for each servo per shot.
* A servo is **only moved when its commanded position differs from the position
  it is already at** (see the worker).  Servos that are not commanded in a shot
  are left untouched -- their value in the shot file is the :data:`~.utils.HOLD`
  sentinel.

Example connection table::

    from user_devices.Motorized_Mounts.labscript_devices import (
        MotorizedMirrorMounts, Mirror, MirrorServo,
    )

    mounts = MotorizedMirrorMounts(
        name='mirror_mounts',
        host='10.11.1.87',   # Arduino IP address
        port=5000,
    )

    # A mirror = two servos (x, y). Bounds are set per axis:
    mot_mirror = Mirror(
        name='mot_mirror',
        parent_device=mounts,
        x_channel=0, y_channel=1,
        x_limits=(20, 180),
        y_limits=(30, 170),
    )

    # A lone servo can also be added directly:
    extra = MirrorServo('extra_servo', mounts, connection=4, limits=(0, 205))

Example experiment::

    mot_mirror.move(x=120, y=95)     # command both axes
    # ... or individually:
    mot_mirror.x.constant(120)
    mot_mirror.y.constant(95)
    extra.constant(100)
"""

import builtins
import keyword

import numpy as np

from labscript import (
    StaticAnalogQuantity,
    IntermediateDevice,
    set_passed_properties,
    LabscriptError,
)

from .utils import (
    get_servo_number,
    servo_connection,
    normalise_mode,
    POSITION_MIN,
    POSITION_MAX,
    HOLD,
    DEFAULT_PRECISION_A,
    DEFAULT_PRECISION_B,
    DEFAULT_MODE,
)


class MirrorServo(StaticAnalogQuantity):
    """A single mirror-mount servo (a static analog output).

    The value commanded is an integer position setpoint.  The full hardware
    range is ``[0, 205]``; pass ``limits=(min, max)`` to restrict the range that
    may be commanded, both from experiment scripts and from the BLACS front
    panel.
    """

    description = "Motorized mirror-mount servo"
    # A servo that is never commanded in a shot must not move; keep the fall-back
    # value inside the hardware range so nothing errors if it is ever read, and
    # detect "not commanded" separately (via `_static_value is None`) when
    # writing the shot file.
    default_value = float(POSITION_MIN)

    @set_passed_properties(
        property_names={
            "connection_table_properties": ["limits", "mirror", "axis"],
        }
    )
    def __init__(self, name, parent_device, connection, limits=None,
                 mirror=None, axis=None, **kwargs):
        """Instantiate a mirror servo.

        Args:
            name (str): labscript name of the servo.
            parent_device (MotorizedMirrorMounts): the controller board.
            connection: servo index (int 0..15) or ``"servo <n>"`` string.
            limits (tuple, optional): ``(min, max)`` position bounds. Defaults
                to the full hardware range ``(0, 205)``. Must lie within it.
            mirror (str, optional): label of the mirror this servo belongs to
                (used only to group widgets in the BLACS front panel).
            axis (str, optional): ``'x'`` or ``'y'`` (front-panel grouping).
        """
        connection = servo_connection(connection)  # validates + normalises

        if limits is None:
            limits = (POSITION_MIN, POSITION_MAX)
        limits = tuple(limits)
        if len(limits) != 2 or limits[0] > limits[1]:
            raise LabscriptError(
                f"{name}: limits must be a (min, max) tuple with min <= max, "
                f"got {limits!r}"
            )
        if limits[0] < POSITION_MIN or limits[1] > POSITION_MAX:
            raise LabscriptError(
                f"{name}: limits {limits!r} lie outside the hardware range "
                f"({POSITION_MIN}, {POSITION_MAX})"
            )

        # Stored so set_passed_properties saves the *resolved* values.
        self.mirror = mirror
        self.axis = axis

        StaticAnalogQuantity.__init__(
            self, name, parent_device, connection, limits=limits, **kwargs
        )

        # Per-shot move-mode override (None => use the controller default).
        self._mode = None

    @property
    def static_value(self):
        """Position for this shot; falls back to :attr:`default_value` silently.

        Overrides the base implementation so that an *uncommanded* servo does
        not emit a warning (the "not commanded -> do not move" case is handled
        explicitly in :meth:`MotorizedMirrorMounts.generate_code`).
        """
        if self._static_value is None:
            return self.default_value
        return self._static_value

    def move(self, position, mode=None, units=None):
        """Command this servo to *position* for the shot.

        Args:
            position: target position setpoint (within this servo's limits).
            mode (str, optional): move mode for this shot -- ``'P'``/``'precision'``,
                ``'A'``/``'auto'`` or ``'S'``/``'set'``. Defaults to the
                controller's ``default_mode``.
            units: units for *position*, per the unit-conversion class (if any).
        """
        self.constant(position, units)
        if mode is not None:
            self._mode = normalise_mode(mode)


class MotorizedMirrorMounts(IntermediateDevice):
    """Arduino servo controller for a cluster of motorized mirror mounts.

    Add :class:`MirrorServo` children directly, or (preferably) group them into
    mirrors with :class:`Mirror`.
    """

    description = "Motorized mirror mounts (Arduino servo controller)"
    allowed_children = [MirrorServo]

    @set_passed_properties(
        property_names={
            "connection_table_properties": [
                "host", "port", "precision_a", "precision_b",
                "default_mode", "mock",
            ],
        }
    )
    def __init__(self, name, host, port=5000,
                 precision_a=DEFAULT_PRECISION_A,
                 precision_b=DEFAULT_PRECISION_B,
                 default_mode=DEFAULT_MODE,
                 mock=False, **kwargs):
        """Instantiate the controller.

        Args:
            name (str): labscript name of the controller.
            host (str): Arduino IP address or hostname.
            port (int): TCP port the firmware listens on (default 5000).
            precision_a (int): constant term of the firmware settle time, in ms.
            precision_b (int): per-step term of the settle time, in ms/step.
                The firmware waits ``precision_a + precision_b * |delta|`` ms
                after commanding a move in precision mode 'P'.
            default_mode (str): default move mode -- ``'P'`` (precision), ``'A'``
                (auto) or ``'S'`` (set). Used when a move does not specify one.
            mock (bool): if True, BLACS simulates the hardware (no TCP).
        """
        # A parentless, top-level device (not clocked by a pseudoclock).
        IntermediateDevice.__init__(self, name, None, **kwargs)
        self.host = host
        self.port = int(port)
        self.precision_a = int(precision_a)
        self.precision_b = int(precision_b)
        self.default_mode = normalise_mode(default_mode)
        self.mock = bool(mock)
        self.BLACS_connection = f"{host}:{port}"

    def add_device(self, device):
        # Validate the connection string and enforce unique servo numbers.
        num = get_servo_number(device.connection)
        for existing in self.child_devices:
            if get_servo_number(existing.connection) == num:
                raise LabscriptError(
                    f"{self.name}: servo number {num} (connection "
                    f"{device.connection!r}) is already used by "
                    f"{existing.name!r}."
                )
        IntermediateDevice.add_device(self, device)

    def generate_code(self, hdf5_file):
        IntermediateDevice.generate_code(self, hdf5_file)

        servos = {servo.connection: servo for servo in self.child_devices}
        if not servos:
            return

        connections = sorted(servos, key=get_servo_number)
        static_value_table = np.empty(
            1, dtype=[(connection, np.int32) for connection in connections]
        )
        # One-character mode code per servo, aligned with static_values.
        static_mode_table = np.empty(
            1, dtype=[(connection, 'S1') for connection in connections]
        )
        for connection, servo in servos.items():
            if servo._static_value is None:
                # Not commanded this shot -> tell the worker to hold position.
                value = HOLD
            else:
                value = int(round(servo._static_value))
            static_value_table[connection][0] = value
            mode = servo._mode if servo._mode is not None else self.default_mode
            static_mode_table[connection][0] = mode.encode('ascii')

        group = self.init_device_group(hdf5_file)
        group.create_dataset('static_values', data=static_value_table)
        group.create_dataset('static_modes', data=static_mode_table)


class Mirror(object):
    """Convenience grouping of the two servos (x, y) that steer one mirror.

    This is a thin helper -- it creates two :class:`MirrorServo` children on the
    given controller and exposes them as :attr:`x` and :attr:`y`.  It is not a
    labscript device itself, so the servos remain direct children of the
    controller (which keeps the BLACS front panel and code generation simple).

    Like a labscript device, the mirror registers itself in the global (builtins)
    namespace under *name*, so it can be referenced by that name from experiment
    scripts and other modules (e.g. ``HODT_mirror.move(...)``).
    """

    def __init__(self, name, parent_device, x_channel, y_channel,
                 x_limits=None, y_limits=None):
        """Create the x and y servos for one mirror.

        Args:
            name (str): base name; servos are named ``<name>_x`` / ``<name>_y``.
            parent_device (MotorizedMirrorMounts): the controller board.
            x_channel: servo index (0..15) for the horizontal axis.
            y_channel: servo index (0..15) for the vertical axis.
            x_limits (tuple, optional): ``(min, max)`` bounds for the x servo.
            y_limits (tuple, optional): ``(min, max)`` bounds for the y servo.
        """
        self._register_name(name)
        self.name = name
        self.x = MirrorServo(
            f"{name}_x", parent_device, x_channel,
            limits=x_limits, mirror=name, axis='x',
        )
        self.y = MirrorServo(
            f"{name}_y", parent_device, y_channel,
            limits=y_limits, mirror=name, axis='y',
        )

    def _register_name(self, name):
        """Expose this mirror in the global namespace, mirroring labscript Devices.

        labscript's compiler snapshots ``builtins`` before the connection table
        runs and removes anything added during a shot compilation, so this entry
        is cleaned up automatically between shots (just like real devices).
        """
        if not isinstance(name, str) or not name.isidentifier() \
                or keyword.iskeyword(name):
            raise LabscriptError(
                f"{name!r} is not a valid Mirror name "
                f"(must be a valid Python identifier)."
            )
        if name in builtins.__dict__:
            raise LabscriptError(
                f"{name} already exists in the Python namespace. "
                f"Please choose another name for this Mirror."
            )
        builtins.__dict__[name] = self

    def move(self, x=None, y=None, mode=None):
        """Command the mirror's axes for this shot.

        Either axis may be omitted; an omitted axis is left untouched (it will
        hold its previous position).

        Args:
            x: target position for the horizontal servo (or ``None``).
            y: target position for the vertical servo (or ``None``).
            mode (str, optional): move mode for both axes this shot -- ``'P'``,
                ``'A'`` or ``'S'`` (defaults to the controller's ``default_mode``).
        """
        if x is not None:
            self.x.move(x, mode=mode)
        if y is not None:
            self.y.move(y, mode=mode)
