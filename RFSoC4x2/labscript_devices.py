"""
labscript device for the RFSoC 4x2 board running the QICK web server.

The RFSoC 4x2 is an RF System-on-Chip board with two DAC outputs (DAC_A,
DAC_B).  This labscript device programs a pulse sequence onto the board and
arms it so that the sequence starts when the parent trigger fires.

Connection-table example::

    from user_devices.RFSoC4x2.labscript_devices import RFSoC4x2, RFSoCChannel

    rfsoc = RFSoC4x2(
        name='rfsoc',
        parent_device=trigger,
        connection='trigger_0',
        server_host='10.14.1.26',
        server_port=8000,
    )
    dac_a = RFSoCChannel('dac_a', parent_device=rfsoc, connection='DAC_A')
    dac_b = RFSoCChannel('dac_b', parent_device=rfsoc, connection='DAC_B')

Experiment-script example::

    # Schedule a Gaussian π-pulse on DAC_A at t = 100 ms
    dac_a.gaussian(t=100e-3, duration_us=1.0, sigma_us=0.2, amplitude=0.8,
                   freq_MHz=100.0)
    # Schedule a WURST sweep on DAC_B
    dac_b.wurst(t=100e-3, duration_us=500.0, bandwidth_MHz=10.0,
                n_factor=20, amplitude=0.9, freq_MHz=200.0)
"""
from __future__ import annotations

import json

import numpy as np

from labscript import Device, LabscriptError, TriggerableDevice, set_passed_properties

# The two DAC channels available on the RFSoC 4x2
CHANNELS = ["DAC_A", "DAC_B"]


class RFSoCChannel(Device):
    """A single DAC output channel on the RFSoC 4x2.

    Attach one instance per DAC channel as a child of :class:`RFSoC4x2` and
    use the pulse-scheduling methods to build up the sequence for each shot.

    Parameters
    ----------
    name:
        Labscript device name.
    parent_device:
        The :class:`RFSoC4x2` instance this channel belongs to.
    connection:
        Must be ``'DAC_A'`` or ``'DAC_B'``.
    """

    description = "RFSoC 4x2 DAC Channel"
    allowed_children = []

    def __init__(self, name: str, parent_device: "RFSoC4x2", connection: str, **kwargs):
        if connection not in CHANNELS:
            raise LabscriptError(
                f"RFSoCChannel connection must be one of {CHANNELS}, got '{connection}'"
            )
        Device.__init__(self, name, parent_device, connection, **kwargs)
        # Pulse events accumulated during the experiment script.
        self.pulses: list[dict] = []
        # Optional channel-level config (set via set_channel_config).
        self.channel_config: dict = {}

    # ------------------------------------------------------------------
    # Channel-level configuration
    # ------------------------------------------------------------------

    def set_channel_config(
        self,
        mixer_freq_MHz: float = 0.0,
        nyquist_zone: int = 1,
        gain: float = 1.0,
        interpolation: int = 1,
    ) -> None:
        """Set channel-level hardware configuration.

        This is sent once per shot alongside the pulse sequence.

        Parameters
        ----------
        mixer_freq_MHz:
            IQ mixer LO frequency in MHz.  ``0.0`` disables the mixer.
        nyquist_zone:
            Nyquist zone (1 or 2) — use zone 2 for frequencies above
            ``fs/2``.
        gain:
            Channel-wide gain factor [0, 1].
        interpolation:
            DAC interpolation factor (integer ≥ 1).
        """
        self.channel_config = {
            "mixer_freq_MHz": float(mixer_freq_MHz),
            "nyquist_zone": int(nyquist_zone),
            "gain": float(gain),
            "interpolation": int(interpolation),
        }

    # ------------------------------------------------------------------
    # Pulse-scheduling methods
    # ------------------------------------------------------------------

    def gaussian(
        self,
        t: float,
        duration_us: float,
        sigma_us: float,
        amplitude: float,
        freq_MHz: float = 0.0,
        gain: float = 1.0,
        phase_deg: float = 0.0,
    ) -> None:
        """Schedule a Gaussian-envelope pulse.

        Parameters
        ----------
        t:
            Start time in labscript seconds.
        duration_us:
            Pulse duration in microseconds.
        sigma_us:
            Gaussian sigma (width) in microseconds.
        amplitude:
            Peak amplitude in [0, 1].
        freq_MHz:
            Carrier frequency in MHz.
        gain:
            Per-pulse gain factor in [0, 1].
        phase_deg:
            Initial carrier phase in degrees.
        """
        self._add_pulse(
            t,
            "gaussian",
            {"duration_us": duration_us, "sigma_us": sigma_us, "amplitude": amplitude},
            freq_MHz=freq_MHz,
            gain=gain,
            phase_deg=phase_deg,
        )

    def drag(
        self,
        t: float,
        duration_us: float,
        sigma_us: float,
        delta: float,
        alpha: float,
        amplitude: float,
        freq_MHz: float = 0.0,
        gain: float = 1.0,
        phase_deg: float = 0.0,
    ) -> None:
        """Schedule a DRAG (Derivative Removal via Adiabatic Gate) pulse.

        Parameters
        ----------
        t:
            Start time in labscript seconds.
        duration_us:
            Pulse duration in microseconds.
        sigma_us:
            Gaussian sigma in microseconds.
        delta:
            Anharmonicity of the transmon (rad/µs).
        alpha:
            DRAG correction coefficient.
        amplitude:
            Peak amplitude in [0, 1].
        freq_MHz:
            Carrier frequency in MHz.
        gain:
            Per-pulse gain factor in [0, 1].
        phase_deg:
            Initial carrier phase in degrees.
        """
        self._add_pulse(
            t,
            "drag",
            {
                "duration_us": duration_us,
                "sigma_us": sigma_us,
                "delta": delta,
                "alpha": alpha,
                "amplitude": amplitude,
            },
            freq_MHz=freq_MHz,
            gain=gain,
            phase_deg=phase_deg,
        )

    def wurst(
        self,
        t: float,
        duration_us: float,
        bandwidth_MHz: float,
        n_factor: int,
        amplitude: float,
        freq_MHz: float = 0.0,
        gain: float = 1.0,
        phase_deg: float = 0.0,
    ) -> None:
        """Schedule a WURST (Wideband, Uniform Rate, Smooth Truncation) pulse.

        Parameters
        ----------
        t:
            Start time in labscript seconds.
        duration_us:
            Pulse duration in microseconds.
        bandwidth_MHz:
            Sweep bandwidth in MHz.
        n_factor:
            WURST smoothness factor (integer ≥ 1; higher → smoother edges).
        amplitude:
            Peak amplitude in [0, 1].
        freq_MHz:
            Centre carrier frequency in MHz.
        gain:
            Per-pulse gain factor in [0, 1].
        phase_deg:
            Initial carrier phase in degrees.
        """
        self._add_pulse(
            t,
            "wurst",
            {
                "duration_us": duration_us,
                "bandwidth_MHz": bandwidth_MHz,
                "n_factor": int(n_factor),
                "amplitude": amplitude,
            },
            freq_MHz=freq_MHz,
            gain=gain,
            phase_deg=phase_deg,
        )

    def cw(
        self,
        t: float,
        duration_us: float,
        amplitude: float,
        freq_MHz: float = 0.0,
        gain: float = 1.0,
        phase_deg: float = 0.0,
    ) -> None:
        """Schedule a rectangular (continuous-wave) pulse.

        Parameters
        ----------
        t:
            Start time in labscript seconds.
        duration_us:
            Pulse duration in microseconds.
        amplitude:
            Amplitude in (0, 1].
        freq_MHz:
            Carrier frequency in MHz.
        gain:
            Per-pulse gain factor in [0, 1].
        phase_deg:
            Initial carrier phase in degrees.
        """
        self._add_pulse(
            t,
            "cw",
            {"duration_us": duration_us, "amplitude": amplitude},
            freq_MHz=freq_MHz,
            gain=gain,
            phase_deg=phase_deg,
        )

    def arbitrary(
        self,
        t: float,
        i_data: "list[float] | np.ndarray",
        q_data: "list[float] | np.ndarray",
        freq_MHz: float = 0.0,
        gain: float = 1.0,
        phase_deg: float = 0.0,
    ) -> None:
        """Schedule an arbitrary IQ waveform.

        Parameters
        ----------
        t:
            Start time in labscript seconds.
        i_data:
            I-quadrature samples, each in [-1, 1].
        q_data:
            Q-quadrature samples, each in [-1, 1].  Must be the same
            length as *i_data*.
        freq_MHz:
            Carrier frequency in MHz.
        gain:
            Per-pulse gain factor in [0, 1].
        phase_deg:
            Initial carrier phase in degrees.
        """
        self._add_pulse(
            t,
            "arbitrary",
            {
                "i_data": [float(v) for v in i_data],
                "q_data": [float(v) for v in q_data],
            },
            freq_MHz=freq_MHz,
            gain=gain,
            phase_deg=phase_deg,
        )

    def zero(self, t: float, duration_us: float) -> None:
        """Schedule a zero (off / padding) pulse.

        Parameters
        ----------
        t:
            Start time in labscript seconds.
        duration_us:
            Duration in microseconds.
        """
        self._add_pulse(t, "zero", {"duration_us": duration_us})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_pulse(
        self,
        t: float,
        waveform_type: str,
        params: dict,
        freq_MHz: float = 0.0,
        gain: float = 1.0,
        phase_deg: float = 0.0,
    ) -> None:
        self.pulses.append(
            {
                "channel": self.connection,
                # labscript time is in seconds; the server expects microseconds
                "t_us": float(t) * 1e6,
                "waveform": {"type": waveform_type, "params": params},
                "gain": float(gain),
                "freq_MHz": float(freq_MHz),
                "phase_deg": float(phase_deg),
            }
        )

    def generate_code(self, hdf5_file) -> None:
        # All data is written by the parent RFSoC4x2.generate_code; nothing
        # to do here.
        pass


# ---------------------------------------------------------------------------


class RFSoC4x2(TriggerableDevice):
    """Labscript device for the RFSoC 4x2 board running the QICK web server.

    This device must be a child of a trigger device.  When the parent fires a
    hardware trigger on the configured connection, the board starts playing the
    pulse sequence that was programmed during ``transition_to_buffered``.

    Parameters
    ----------
    name:
        Labscript device name.
    parent_device:
        The device that provides the hardware trigger.
    connection:
        The output channel on *parent_device* used to trigger this board.
    server_host:
        IP address or hostname of the QICK web server.
    server_port:
        TCP port of the QICK web server (default 8000).
    mock:
        If ``True``, all HTTP calls are skipped (useful for offline testing).
    """

    description = "RFSoC 4x2"
    allowed_children = [RFSoCChannel]

    @set_passed_properties(
        property_names={
            "connection_table_properties": [
                "server_host",
                "server_port",
                "mock",
            ],
        }
    )
    def __init__(
        self,
        name: str,
        parent_device,
        connection: str,
        server_host: str,
        server_port: int = 8000,
        mock: bool = False,
        **kwargs,
    ):
        self.BLACS_connection = f"{server_host}:{server_port}"
        TriggerableDevice.__init__(self, name, parent_device, connection, **kwargs)

    # ------------------------------------------------------------------

    def generate_code(self, hdf5_file) -> None:
        group = self.init_device_group(hdf5_file)

        # Gather pulses and channel configs from child channels
        pulses: list[dict] = []
        channel_configs: dict[str, dict] = {}
        for child in self.child_devices:
            if isinstance(child, RFSoCChannel):
                pulses.extend(child.pulses)
                if child.channel_config:
                    channel_configs[child.connection] = child.channel_config

        # Sort by start time so the server receives them in chronological order
        pulses.sort(key=lambda p: p["t_us"])

        sequence = {
            "pulses": pulses,
            "channel_configs": channel_configs,
            "readout_config": None,
        }

        # Store the sequence as a JSON string attribute on the device group.
        # For very large arbitrary waveforms the user may want to split shots,
        # but typical sequences fit comfortably in an HDF5 attribute.
        group.attrs["pulse_sequence"] = json.dumps(sequence)
