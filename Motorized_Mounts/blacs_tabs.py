#####################################################################
#                                                                   #
# user_devices/Motorized_Mounts/blacs_tabs.py                       #
#                                                                   #
# BLACS front-panel tab for the motorized mirror mounts.            #
#                                                                   #
#####################################################################
"""BLACS front panel for the motorized mirror mounts.

Each servo gets a standard analog-output spinbox (bounded by the servo's
``limits``).  Servos are grouped in the panel by the mirror they belong to.
The Controls panel provides:

* a **move-mode selector** (Precision 'P' / Auto 'A' / Set 'S') that all manual
  (front-panel) moves use;
* **Servos ON / OFF** buttons (servo power; useful with the 'S' mode);
* a **Query current positions** button that forces a fresh read of every servo.
"""

from blacs.device_base_class import DeviceTab, define_state, MODE_MANUAL
from qtutils import inmain_decorator
from qtutils.qt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
)

from .utils import (
    get_servo_number, normalise_mode,
    DEFAULT_PRECISION_A, DEFAULT_PRECISION_B, DEFAULT_MODE,
    MODES, MODE_LABELS,
)


class MotorizedMirrorMountsTab(DeviceTab):

    def initialise_GUI(self):
        device = self.settings['connection_table'].find_by_name(self.device_name)
        props = device.properties

        self.host = props['host']
        self.port = props.get('port', 5000)
        self.mock = props.get('mock', False)
        self.precision_a = props.get('precision_a', DEFAULT_PRECISION_A)
        self.precision_b = props.get('precision_b', DEFAULT_PRECISION_B)
        self.default_mode = normalise_mode(props.get('default_mode', DEFAULT_MODE))
        # Current front-panel move mode (restored from saved data if available).
        self._manual_mode = self.default_mode

        # Build the analog-output properties and remember per-servo metadata.
        ao_prop = {}
        self.child_limits = {}
        self._servo_meta = {}  # connection -> (mirror, axis)
        for servo in device.child_list.values():
            connection = servo.parent_port
            lo, hi = servo.properties['limits']
            lo, hi = int(lo), int(hi)
            ao_prop[connection] = {
                'base_unit': 'pos',
                'min': lo,
                'max': hi,
                'step': 1,
                'decimals': 0,
            }
            self.child_limits[connection] = (lo, hi)
            self._servo_meta[connection] = (
                servo.properties.get('mirror'),
                servo.properties.get('axis'),
            )

        # Deterministic ordering by servo number.
        ao_prop = {c: ao_prop[c] for c in sorted(ao_prop, key=get_servo_number)}
        self.child_connections = list(ao_prop.keys())

        # Create the AO output objects and their widgets (with nice labels).
        self.create_analog_outputs(ao_prop)
        widget_props = {
            conn: {'display_name': self._display_name(conn)}
            for conn in ao_prop
        }
        ao_widgets = self.create_analog_widgets(widget_props)

        # Group widgets by mirror and place them, plus a small controls panel.
        groups = self._group_widgets(ao_widgets)
        groups.append(("Controls", {'controls': self._make_controls_widget()}))
        self.auto_place_widgets(*groups)

        # Capabilities: we can read positions back, and we skip unchanged moves.
        self.supports_remote_value_check(True)
        self.supports_smart_programming(True)

    def initialise_workers(self):
        self.create_worker(
            "main_worker",
            "user_devices.Motorized_Mounts.blacs_workers.MotorizedMirrorMountsWorker",
            {
                'host': self.host,
                'port': self.port,
                'mock': self.mock,
                'precision_a': self.precision_a,
                'precision_b': self.precision_b,
                'default_mode': self.default_mode,
                'child_connections': self.child_connections,
                'child_limits': self.child_limits,
            },
        )
        self.primary_worker = "main_worker"
        # Push the restored manual mode to the worker on start-up.
        self.set_manual_mode(self._manual_mode)

    # -- widget helpers -----------------------------------------------------

    def _display_name(self, connection):
        mirror, axis = self._servo_meta.get(connection, (None, None))
        if mirror:
            axis_label = axis.upper() if axis else '?'
            return f"{mirror} {axis_label}\n({connection})"
        return connection

    def _group_widgets(self, ao_widgets):
        """Partition AO widgets into per-mirror groups (plus lone servos)."""
        mirror_groups = {}   # mirror label -> {connection: widget}
        loose = {}           # servos with no mirror label
        for connection, widget in ao_widgets.items():
            mirror, _axis = self._servo_meta.get(connection, (None, None))
            if mirror:
                mirror_groups.setdefault(mirror, {})[connection] = widget
            else:
                loose[connection] = widget

        groups = []
        for mirror in sorted(mirror_groups):
            groups.append(
                (f"Mirror: {mirror}", mirror_groups[mirror], get_servo_number)
            )
        if loose:
            groups.append(("Servos", loose, get_servo_number))
        return groups

    def _make_controls_widget(self):
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 4, 4, 4)

        info = QLabel(f"Arduino @ {self.host}:{self.port}"
                      + ("  (MOCK)" if self.mock else ""))
        info.setStyleSheet('color: gray;')
        vbox.addWidget(info)

        # Move-mode selector (applies to manual/front-panel moves).
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Move mode:"))
        self._mode_combo = QComboBox()
        for mode in MODES:
            self._mode_combo.addItem(MODE_LABELS[mode], mode)
        idx = self._mode_combo.findData(self._manual_mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)
        self._mode_combo.setToolTip(
            "Firmware command used for front-panel moves:\n"
            "  P precision - power on, move, tunable settle, power off\n"
            "  A auto      - power on, move, fixed settle, power off\n"
            "  S set       - move only; use the ON/OFF buttons for power"
        )
        self._mode_combo.currentIndexChanged.connect(
            lambda *_: self.on_mode_changed()
        )
        mode_row.addWidget(self._mode_combo, stretch=1)
        vbox.addLayout(mode_row)

        # Servo power + query buttons.
        row = QHBoxLayout()
        btn_on = QPushButton("Servos ON")
        btn_on.setToolTip("Switch all servo power on.")
        btn_on.clicked.connect(lambda: self.set_power(True))
        btn_off = QPushButton("Servos OFF")
        btn_off.setToolTip("Switch all servo power off.")
        btn_off.clicked.connect(lambda: self.set_power(False))
        btn_query = QPushButton("Query current positions")
        btn_query.setToolTip(
            "Read every servo's position from the hardware and update the "
            "front panel. Positions are otherwise only queried on start-up or "
            "when they become unclear."
        )
        btn_query.clicked.connect(lambda: self.requery_positions())
        row.addWidget(btn_on)
        row.addWidget(btn_off)
        row.addWidget(btn_query)
        row.addStretch()
        vbox.addLayout(row)

        return container

    def _selected_mode(self):
        return self._mode_combo.currentData()

    # -- front-panel actions ------------------------------------------------

    def on_mode_changed(self):
        self._manual_mode = self._selected_mode()
        self.set_manual_mode(self._manual_mode)

    @define_state(MODE_MANUAL, True)
    def set_manual_mode(self, mode):
        """Tell the worker which move mode to use for manual moves."""
        yield (self.queue_work(self._primary_worker, 'set_manual_mode', mode))

    @define_state(MODE_MANUAL, True)
    def set_power(self, on):
        """Switch all servo power on/off."""
        yield (self.queue_work(
            self._primary_worker, 'power_on' if on else 'power_off'
        ))

    @define_state(MODE_MANUAL, True)
    def requery_positions(self):
        """Force a fresh Get of every servo and update the widgets."""
        values = yield (self.queue_work(self._primary_worker, 'requery'))
        if isinstance(values, dict):
            self._apply_values_to_widgets(values)

    @inmain_decorator(True)
    def _apply_values_to_widgets(self, values):
        for connection, value in values.items():
            if connection in self._AO:
                self._AO[connection].set_value(value, program=False)

    # -- persistence (save/restore the selected move mode) ------------------

    def get_save_data(self):
        return {'manual_mode': self._manual_mode}

    def restore_save_data(self, data):
        mode = (data or {}).get('manual_mode')
        if mode in MODES:
            self._manual_mode = mode
            if hasattr(self, '_mode_combo'):
                idx = self._mode_combo.findData(mode)
                if idx >= 0:
                    # Avoid re-triggering on_mode_changed during restore.
                    self._mode_combo.blockSignals(True)
                    self._mode_combo.setCurrentIndex(idx)
                    self._mode_combo.blockSignals(False)
