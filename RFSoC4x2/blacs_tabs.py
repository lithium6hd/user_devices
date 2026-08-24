"""
BLACS tab (GUI front-panel) for the RFSoC 4x2 device.

Layout
------
The tab is divided into four sections:

1. **Status** – shows whether the server is reachable, the board armed /
   running state, and the queue position.
2. **Controls** – Arm, Software-Trigger, Abort, and Refresh Status buttons
   (all disabled during a buffered shot to prevent interference with the
   running experiment).
3. **DAC_A CW** / **DAC_B CW** – manual continuous-wave controls for each
   channel so the user can send a steady-state tone for debugging /
   alignment without writing an experiment script.
4. A **Stop All CW** button to silence both channels at once.
"""
from __future__ import annotations

from blacs.device_base_class import (
    DeviceTab,
    MODE_MANUAL,
    define_state,
)
from qtutils.qt import QtWidgets


# Nyquist-zone labels shown in the combo-boxes
_NZ_LABELS = [("Zone 1", 1), ("Zone 2", 2)]

# CW mode options
_CW_MODES = ["auto", "dds", "mixer"]


class RFSoC4x2Tab(DeviceTab):
    """BLACS front-panel for the RFSoC 4x2 board."""

    # -----------------------------------------------------------------------
    # Initialisation
    # -----------------------------------------------------------------------

    def initialise_GUI(self) -> None:
        # Retrieve connection-table properties written by labscript_devices.py
        connection_table = self.settings["connection_table"]
        props = connection_table.find_by_name(self.device_name).properties

        server_host = props.get("server_host", "")
        server_port = int(props.get("server_port", 8000))
        mock = bool(props.get("mock", False))

        # Create the worker process
        self.create_worker(
            "main_worker",
            "user_devices.RFSoC4x2.blacs_workers.RFSoC4x2Worker",
            {
                "server_host": server_host,
                "server_port": server_port,
                "mock": mock,
            },
        )
        self.primary_worker = "main_worker"

        # This device has no standard labscript output widgets
        self.supports_remote_value_check(False)
        self.supports_smart_programming(False)

        # ------------------------------------------------------------------
        # Build the Qt widget tree
        # ------------------------------------------------------------------
        root = QtWidgets.QWidget()
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setSpacing(6)

        # -- Status group --------------------------------------------------
        status_group = QtWidgets.QGroupBox("Status")
        status_form = QtWidgets.QFormLayout(status_group)
        status_form.setHorizontalSpacing(12)

        self._lbl_server = QtWidgets.QLabel(f"{server_host}:{server_port}")
        self._lbl_connected = _indicator_label("---")
        self._lbl_armed = _indicator_label("---")
        self._lbl_running = _indicator_label("---")
        self._lbl_queue = _indicator_label("---")
        self._lbl_cw = _indicator_label("---")

        status_form.addRow("Server:", self._lbl_server)
        status_form.addRow("Connected:", self._lbl_connected)
        status_form.addRow("Armed:", self._lbl_armed)
        status_form.addRow("Running:", self._lbl_running)
        status_form.addRow("Queue (idx/len):", self._lbl_queue)
        status_form.addRow("CW active:", self._lbl_cw)

        root_layout.addWidget(status_group)

        # -- Controls group ------------------------------------------------
        ctrl_group = QtWidgets.QGroupBox("Controls")
        ctrl_layout = QtWidgets.QHBoxLayout(ctrl_group)

        self._btn_arm = QtWidgets.QPushButton("Arm")
        self._btn_trigger = QtWidgets.QPushButton("Software Trigger")
        self._btn_abort = QtWidgets.QPushButton("Abort")
        self._btn_refresh = QtWidgets.QPushButton("Refresh Status")

        for btn in (self._btn_arm, self._btn_trigger, self._btn_abort, self._btn_refresh):
            btn.setMinimumWidth(110)
            ctrl_layout.addWidget(btn)

        ctrl_layout.addStretch()
        root_layout.addWidget(ctrl_group)

        # -- Per-channel CW controls ---------------------------------------
        self._cw_widgets: dict[str, dict] = {}
        for channel in ("DAC_A", "DAC_B"):
            cw_group = QtWidgets.QGroupBox(f"CW — {channel}")
            cw_layout = QtWidgets.QHBoxLayout(cw_group)
            cw_layout.setSpacing(8)

            enable_cb = QtWidgets.QCheckBox("Enable")
            enable_cb.setToolTip(
                "Check to start a CW tone; uncheck and click Apply to stop."
            )

            freq_spin = QtWidgets.QDoubleSpinBox()
            freq_spin.setRange(0.0, 6000.0)
            freq_spin.setDecimals(3)
            freq_spin.setSuffix(" MHz")
            freq_spin.setValue(100.0)
            freq_spin.setMinimumWidth(110)

            amp_spin = QtWidgets.QDoubleSpinBox()
            amp_spin.setRange(0.01, 1.0)
            amp_spin.setDecimals(3)
            amp_spin.setSingleStep(0.01)
            amp_spin.setValue(0.5)
            amp_spin.setMinimumWidth(70)

            nz_combo = QtWidgets.QComboBox()
            for label, value in _NZ_LABELS:
                nz_combo.addItem(label, value)

            mode_combo = QtWidgets.QComboBox()
            for m in _CW_MODES:
                mode_combo.addItem(m)

            apply_btn = QtWidgets.QPushButton("Apply")
            apply_btn.setMinimumWidth(60)

            cw_layout.addWidget(enable_cb)
            cw_layout.addWidget(QtWidgets.QLabel("Freq:"))
            cw_layout.addWidget(freq_spin)
            cw_layout.addWidget(QtWidgets.QLabel("Amp:"))
            cw_layout.addWidget(amp_spin)
            cw_layout.addWidget(QtWidgets.QLabel("Nyquist:"))
            cw_layout.addWidget(nz_combo)
            cw_layout.addWidget(QtWidgets.QLabel("Mode:"))
            cw_layout.addWidget(mode_combo)
            cw_layout.addWidget(apply_btn)
            cw_layout.addStretch()

            self._cw_widgets[channel] = {
                "enable": enable_cb,
                "freq": freq_spin,
                "amp": amp_spin,
                "nz": nz_combo,
                "mode": mode_combo,
                "apply": apply_btn,
            }
            root_layout.addWidget(cw_group)

        # -- Stop All CW ---------------------------------------------------
        stop_all_row = QtWidgets.QHBoxLayout()
        self._btn_stop_cw = QtWidgets.QPushButton("Stop All CW")
        self._btn_stop_cw.setMinimumWidth(110)
        stop_all_row.addWidget(self._btn_stop_cw)
        stop_all_row.addStretch()
        root_layout.addLayout(stop_all_row)

        root_layout.addStretch()

        # ------------------------------------------------------------------
        # Wire up signals
        # ------------------------------------------------------------------
        self._btn_arm.clicked.connect(self.arm)
        self._btn_trigger.clicked.connect(self.software_trigger)
        self._btn_abort.clicked.connect(self.abort)
        self._btn_refresh.clicked.connect(self.refresh_status)
        self._btn_stop_cw.clicked.connect(self.stop_cw)

        for channel, widgets in self._cw_widgets.items():
            # Use a default-argument trick to capture the channel name
            widgets["apply"].clicked.connect(
                lambda _checked, ch=channel: self.apply_cw(ch)
            )

        # Place the root widget in the BLACS tab
        self.get_tab_layout().addWidget(root)

    # -----------------------------------------------------------------------
    # State-machine methods (run in the BLACS event loop)
    # -----------------------------------------------------------------------

    @define_state(MODE_MANUAL, True)
    def refresh_status(self) -> None:
        """Refresh the status labels from the server."""
        status = yield self.queue_work(self.primary_worker, "get_status")
        self._apply_status(status)

    @define_state(MODE_MANUAL, True)
    def arm(self) -> None:
        """Arm the board (it will start on the next hardware trigger)."""
        yield self.queue_work(self.primary_worker, "arm")
        status = yield self.queue_work(self.primary_worker, "get_status")
        self._apply_status(status)

    @define_state(MODE_MANUAL, True)
    def software_trigger(self) -> None:
        """Issue a software trigger (bypasses the hardware trigger line)."""
        yield self.queue_work(self.primary_worker, "trigger")
        status = yield self.queue_work(self.primary_worker, "get_status")
        self._apply_status(status)

    @define_state(MODE_MANUAL, True)
    def abort(self) -> None:
        """Abort the current sequence and return the board to idle."""
        yield self.queue_work(self.primary_worker, "abort")
        status = yield self.queue_work(self.primary_worker, "get_status")
        self._apply_status(status)

    @define_state(MODE_MANUAL, True)
    def apply_cw(self, channel: str) -> None:
        """Start or stop a CW tone on *channel* depending on the Enable checkbox."""
        widgets = self._cw_widgets[channel]
        if widgets["enable"].isChecked():
            yield self.queue_work(
                self.primary_worker,
                "start_cw",
                channel,
                widgets["freq"].value(),
                widgets["amp"].value(),
                widgets["nz"].currentData(),
                widgets["mode"].currentText(),
            )
        else:
            yield self.queue_work(self.primary_worker, "stop_cw")
        status = yield self.queue_work(self.primary_worker, "get_status")
        self._apply_status(status)

    @define_state(MODE_MANUAL, True)
    def stop_cw(self) -> None:
        """Stop the CW tone on all channels and uncheck the Enable boxes."""
        yield self.queue_work(self.primary_worker, "stop_cw")
        for widgets in self._cw_widgets.values():
            widgets["enable"].setChecked(False)
        status = yield self.queue_work(self.primary_worker, "get_status")
        self._apply_status(status)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _apply_status(self, status: dict | None) -> None:
        """Update all status labels from a status response dict."""
        if not status:
            self._lbl_connected.setText("Unreachable")
            self._lbl_connected.setStyleSheet("color: red;")
            return

        self._lbl_connected.setText("OK")
        self._lbl_connected.setStyleSheet("color: green;")

        armed = status.get("armed", False)
        self._lbl_armed.setText("Yes" if armed else "No")
        self._lbl_armed.setStyleSheet("color: green;" if armed else "color: gray;")

        running = status.get("running", False)
        self._lbl_running.setText("Yes" if running else "No")
        self._lbl_running.setStyleSheet("color: green;" if running else "color: gray;")

        q_idx = status.get("queue_index", 0)
        q_len = status.get("queue_length", 0)
        self._lbl_queue.setText(f"{q_idx} / {q_len}")

        cw = status.get("cw_active", False)
        self._lbl_cw.setText("Yes" if cw else "No")
        self._lbl_cw.setStyleSheet("color: orange;" if cw else "color: gray;")

    # -----------------------------------------------------------------------
    # Standard DeviceTab overrides
    # -----------------------------------------------------------------------

    def get_all_hardware_values(self) -> dict:
        """No standard output widgets — return an empty dict."""
        return {}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _indicator_label(text: str) -> QtWidgets.QLabel:
    """Return a QLabel styled as a read-only status indicator."""
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet("color: gray;")
    return lbl
