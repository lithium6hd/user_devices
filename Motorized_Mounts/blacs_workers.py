#####################################################################
#                                                                   #
# user_devices/Motorized_Mounts/blacs_workers.py                    #
#                                                                   #
# BLACS worker for the motorized mirror mounts.                     #
#                                                                   #
#####################################################################
"""BLACS worker for the Arduino servo controller (servocontrol_v1_87).

Wire protocol (each command is terminated by ``\\n``)::

    P<n>:<pos>;<a>,<b>   precision move: power on, drive servo <n> to <pos>,
                         wait a + b*|delta| ms, power off.
                         reply:  "<n>(auto) :<pos>\\n"
    Get<n>               query last commanded position of servo <n>.
                         reply:  3-digit position, e.g. "120"
                                 (or "410" if never commanded since power-up,
                                  or "E01"/"E02" on error)
    S<n>:<pos>           set position without touching servo power.
    A<n>:<pos>           auto move (power on/off) with a fixed settle time.
    on / off             switch all servo power on / off.

Position-tracking strategy (this is what implements the user's requirements):

* We keep a best-known position per servo in ``self._positions`` (``None`` means
  "unclear").  All servos start ``None`` at BLACS start-up.
* ``check_remote_values`` is called by BLACS at start-up and periodically.  It
  performs a real ``Get`` **only** for servos whose position is currently
  unclear; otherwise it returns the cached value.  So ``Get`` is issued on
  start-up (all unclear) but not on every poll.
* When programming (manual or buffered), a servo is moved **only if** the target
  differs from its known position.  If the position is unclear we first ``Get``
  it, so we can decide; if it is still unclear afterwards (e.g. the firmware
  reports the never-commanded sentinel) we move to establish it.
* After a successful move we assume the servo reached the target (as instructed)
  and update the cache -- no confirmation ``Get`` is needed.
* Servos whose shot value is the :data:`~.utils.HOLD` sentinel (i.e. not
  commanded in that shot) are left untouched.
"""

import socket
from time import monotonic

from blacs.tab_base_classes import Worker
import labscript_utils.h5_lock  # noqa: F401  (installs the hdf5 lock)
import h5py

from .utils import (
    get_servo_number,
    normalise_mode,
    POSITION_MIN,
    POSITION_MAX,
    HOLD,
    MODE_PRECISION,
    MODE_AUTO,
    MODE_SET,
)


class MockServoInterface(object):
    """Simulated controller used when ``mock=True`` (no hardware/TCP)."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        # None => "never commanded since power-up" (like the firmware sentinel).
        self._positions = {}
        self.powered = False

    def get_position(self, num):
        pos = self._positions.get(num)
        print(f"[MOCK] Get{num} -> {pos}")
        return pos

    def move_precision(self, num, pos, a, b):
        print(f"[MOCK] P{num}:{pos};{a},{b}")
        self._positions[num] = pos
        return pos

    def move_auto(self, num, pos):
        print(f"[MOCK] A{num}:{pos}")
        self._positions[num] = pos
        return pos

    def move_set(self, num, pos):
        print(f"[MOCK] S{num}:{pos}")
        self._positions[num] = pos
        return pos

    def power(self, on):
        print(f"[MOCK] {'on' if on else 'off'}")
        self.powered = bool(on)

    def move(self, mode, num, pos, a, b):
        mode = normalise_mode(mode)
        if mode == MODE_PRECISION:
            return self.move_precision(num, pos, a, b)
        if mode == MODE_AUTO:
            return self.move_auto(num, pos)
        return self.move_set(num, pos)

    def close(self):
        pass


class ArduinoServoInterface(object):
    """TCP client for the Arduino servo controller.

    Keeps a single persistent connection open (the firmware serves one client at
    a time in a blocking loop) and transparently reconnects on error.
    """

    def __init__(self, host, port, connect_timeout=5.0):
        self.host = host
        self.port = int(port)
        self.connect_timeout = connect_timeout
        self._sock = None

    # -- connection management ---------------------------------------------

    def _connect(self):
        self.close()
        self._sock = socket.create_connection(
            (self.host, self.port), timeout=self.connect_timeout
        )

    def _ensure_connected(self):
        if self._sock is None:
            self._connect()

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- low-level transaction ---------------------------------------------

    def _transaction(self, command, wait_for_newline, read_timeout):
        """Send *command* (``\\n`` appended) and return the stripped reply.

        Reconnects and retries once if the socket has gone stale.
        """
        last_exc = None
        for attempt in range(2):
            try:
                self._ensure_connected()
                self._sock.sendall((command + "\n").encode("ascii"))
                return self._read_reply(wait_for_newline, read_timeout)
            except (OSError, socket.timeout) as exc:
                last_exc = exc
                self.close()  # force a fresh connection on the retry
        raise ConnectionError(
            f"Communication with servo controller at {self.host}:{self.port} "
            f"failed: {last_exc}"
        )

    def _read_reply(self, wait_for_newline, read_timeout):
        """Accumulate the reply until it is complete or *read_timeout* elapses.

        The firmware terminates the precision-move reply with a newline but sends
        the short 3-character replies (position / "ack" / "E0x") without one, so
        the caller says which terminator to expect.
        """
        data = b""
        deadline = monotonic() + read_timeout
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(64)
            except socket.timeout:
                break
            if not chunk:
                break  # peer closed the connection
            data += chunk
            if wait_for_newline:
                if b"\n" in data:
                    break
            elif len(data.strip()) >= 3:
                break
        return data.decode("ascii", errors="replace").strip()

    # -- high-level commands -----------------------------------------------

    def get_position(self, num):
        """Return the servo's last-commanded position, or ``None`` if unclear.

        ``None`` is returned for the never-commanded sentinel (410), an error
        reply, or an unparseable reply.
        """
        reply = self._transaction(f"Get{num}", wait_for_newline=False,
                                   read_timeout=3.0)
        reply = reply.strip()
        if not reply or reply.startswith("E"):
            return None
        try:
            value = int(reply)
        except ValueError:
            return None
        if POSITION_MIN <= value <= POSITION_MAX:
            return value
        return None  # e.g. the 410 "never commanded" sentinel

    def move_precision(self, num, pos, a, b):
        """Drive servo *num* to *pos* using precision mode 'P'."""
        # The firmware blocks for a + b*|delta| ms before replying; size the
        # read timeout for the worst-case full-range move plus overhead.
        settle_ms = a + b * (POSITION_MAX - POSITION_MIN)
        read_timeout = settle_ms / 1000.0 + 3.0
        reply = self._transaction(
            f"P{num}:{pos};{a},{b}", wait_for_newline=True,
            read_timeout=read_timeout,
        )
        if "(auto)" not in reply:
            raise RuntimeError(
                f"Unexpected reply to precision move (P) of servo {num}: "
                f"{reply!r} (E00 means the firmware does not implement 'P'; "
                f"try mode 'A' or 'S')"
            )
        return pos

    def move_auto(self, num, pos):
        """Drive servo *num* to *pos* using auto mode 'A' (fixed settle time)."""
        # Firmware settle for 'A' is 200 + 1.5*|delta| ms; size the timeout for
        # the worst case plus overhead.
        read_timeout = (200 + 1.5 * (POSITION_MAX - POSITION_MIN)) / 1000.0 + 3.0
        reply = self._transaction(
            f"A{num}:{pos}", wait_for_newline=False, read_timeout=read_timeout,
        )
        self._require_ack("auto move (A)", num, reply)
        return pos

    def move_set(self, num, pos):
        """Set servo *num* to *pos* using mode 'S' (does not change power)."""
        reply = self._transaction(
            f"S{num}:{pos}", wait_for_newline=False, read_timeout=3.0,
        )
        self._require_ack("set move (S)", num, reply)
        return pos

    def move(self, mode, num, pos, a, b):
        """Dispatch a move for servo *num* to *pos* using the given *mode*."""
        mode = normalise_mode(mode)
        if mode == MODE_PRECISION:
            return self.move_precision(num, pos, a, b)
        if mode == MODE_AUTO:
            return self.move_auto(num, pos)
        return self.move_set(num, pos)

    def power(self, on):
        """Switch all servo power on or off ('on'/'off' commands)."""
        reply = self._transaction(
            "on" if on else "off", wait_for_newline=False, read_timeout=3.0,
        )
        if reply.strip().lower() != "ack":
            raise RuntimeError(
                f"Unexpected reply to servo power "
                f"{'on' if on else 'off'}: {reply!r}"
            )

    @staticmethod
    def _require_ack(what, num, reply):
        if reply.strip().lower() != "ack":
            raise RuntimeError(
                f"Unexpected reply to {what} of servo {num}: {reply!r} "
                f"(E00=unknown command, E01=out of range, E02=bad format)"
            )


class MotorizedMirrorMountsWorker(Worker):

    def init(self):
        # Workerargs (set by the tab): host, port, mock, precision_a,
        # precision_b, default_mode, child_connections, child_limits.
        self.precision_a = int(self.precision_a)
        self.precision_b = int(self.precision_b)
        # Mode used for manual (front-panel) moves; the front panel can change it.
        self._manual_mode = normalise_mode(getattr(self, "default_mode", None))

        # Map servo number <-> connection, and remember per-servo bounds.
        self._num_to_conn = {}
        self._min_by_num = {}
        self._limits_by_num = {}
        for conn in self.child_connections:
            num = get_servo_number(conn)
            self._num_to_conn[num] = conn
            lo, hi = self.child_limits[conn]
            self._min_by_num[num] = int(lo)
            self._limits_by_num[num] = (int(lo), int(hi))

        # Best-known position per servo; None => unclear (queried on demand).
        self._positions = {num: None for num in self._num_to_conn}

        if self.mock:
            self.intf = MockServoInterface(self.host, self.port)
        else:
            self.intf = ArduinoServoInterface(self.host, self.port)

    # -- position helpers ---------------------------------------------------

    def _refresh(self, num):
        """Query the hardware for servo *num* and update the cache."""
        try:
            self._positions[num] = self.intf.get_position(num)
        except Exception as exc:
            self.logger.warning(f"Get position failed for servo {num}: {exc}")
            self._positions[num] = None
        return self._positions[num]

    def _report_value(self, num):
        """A concrete, in-range value to show in the front panel for *num*."""
        pos = self._positions.get(num)
        if pos is not None:
            return pos
        # Unclear: report the servo's lower bound as a neutral placeholder.
        return self._min_by_num.get(num, POSITION_MIN)

    def _clamp(self, num, target):
        lo, hi = self._limits_by_num.get(num, (POSITION_MIN, POSITION_MAX))
        target = max(POSITION_MIN, min(POSITION_MAX, target))
        return max(lo, min(hi, target))

    def _move_if_needed(self, num, target, mode):
        """Move servo *num* to *target* in *mode*, only if it is not already there."""
        target = self._clamp(num, target)
        mode = normalise_mode(mode)

        known = self._positions.get(num)
        if known is None:
            # Position unclear -> establish it before deciding (Get on demand).
            known = self._refresh(num)

        if known is not None and known == target:
            return  # already at target -> do not move

        try:
            self.intf.move(mode, num, target, self.precision_a,
                           self.precision_b)
            # Assume the servo reached the commanded position.
            self._positions[num] = target
        except Exception as exc:
            self.logger.error(
                f"Move of servo {num} to {target} (mode {mode}) failed: {exc}"
            )
            self._positions[num] = None  # position is now unclear

    def _program(self, values, modes=None):
        """Apply a ``{connection: target}`` mapping and return final values.

        *modes* is an optional ``{connection: mode}`` mapping; connections not in
        it use the current manual mode.
        """
        modes = modes or {}
        final = {}
        for conn, raw in values.items():
            num = get_servo_number(conn)
            target = int(round(float(raw)))
            if target != HOLD:  # HOLD => not commanded this shot; leave as-is
                mode = modes.get(conn, self._manual_mode)
                self._move_if_needed(num, target, mode)
            final[conn] = self._report_value(num)
        return final

    # -- BLACS worker interface --------------------------------------------

    def check_remote_values(self):
        """Return current positions, querying the hardware only when unclear.

        Called by BLACS at start-up and periodically.  This is where the
        start-up ``Get`` happens (every servo starts unclear), while steady-state
        polls are free (cached values, no TCP).
        """
        values = {}
        for num, conn in self._num_to_conn.items():
            if self._positions.get(num) is None:
                self._refresh(num)
            values[conn] = self._report_value(num)
        return values

    def requery(self):
        """Force a fresh ``Get`` of every servo (used by the front-panel button)."""
        for num in self._positions:
            self._positions[num] = None
        return self.check_remote_values()

    def set_manual_mode(self, mode):
        """Set the move mode used for front-panel (manual) moves."""
        self._manual_mode = normalise_mode(mode)
        return self._manual_mode

    def power_on(self):
        """Switch all servo power on (front-panel button / mode 'S' workflow)."""
        try:
            self.intf.power(True)
        except Exception as exc:
            self.logger.error(f"Servo power-on failed: {exc}")
        return True

    def power_off(self):
        """Switch all servo power off (front-panel button)."""
        try:
            self.intf.power(False)
        except Exception as exc:
            self.logger.error(f"Servo power-off failed: {exc}")
        return True

    def program_manual(self, front_panel_values):
        # All manual moves use the currently-selected front-panel mode.
        return self._program(front_panel_values)

    def transition_to_buffered(self, device_name, h5file, initial_values, fresh):
        with h5py.File(h5file, "r") as f:
            group = f["devices"][device_name]
            if "static_values" in group:
                data = group["static_values"][:]
                values = {name: int(data[name][0]) for name in data.dtype.names}
            else:
                values = {}
            if "static_modes" in group:
                mdata = group["static_modes"][:]
                modes = {
                    name: mdata[name][0].decode("ascii")
                    for name in mdata.dtype.names
                }
            else:
                modes = {}
        if fresh:
            # A forced full reprogram (first shot, or "clear smart programming"):
            # drop the cached position of every servo we are about to command so
            # that we re-sync it from the hardware (Get) before deciding whether
            # to move. Held (uncommanded) servos keep their cached position so
            # their reported value stays correct. `fresh` is only set on such
            # explicit events -- never on every shot -- so this does not defeat
            # the "don't Get every shot" behaviour.
            for conn, raw in values.items():
                if int(round(float(raw))) != HOLD:
                    self._positions[get_servo_number(conn)] = None
        return self._program(values, modes)

    def transition_to_manual(self):
        return True

    def abort_buffered(self):
        return True

    def abort_transition_to_buffered(self):
        return True

    def shutdown(self):
        self.intf.close()
