from blacs.device_base_class import DeviceTab

class StaticLV2SLMTab(DeviceTab):
    """BLACS front-panel for DDS_AS065 following ADwin-style patterns.

    Provides minimal controls wired to the worker:
    - Arm / Disarm
    - Trigger
    - Start Auto (dwell ms)
    - Stop Auto
    - Status query and display
    """

    worker_class = 'user_devices.StaticLV2SLM.blacs_workers.StaticLV2SLM_Worker'

    def initialise_GUI(self):
        connection_table = self.settings['connection_table']
        props = connection_table.find_by_name(self.device_name).properties

        # Note: This device is programmed via connection table, not manual DDS outputs
        # So we don't create the freq/amp/phase widgets
        
        # If you want DDS output widgets in the future, you'll need to:
        # 1. Add freq/amp/phase/gate as connection_table_properties in labscript_devices.py
        # 2. Pass them in the connection table
        # For now, no widgets needed as device is fully programmed

        # Create the worker with connection properties
        self.create_worker(
            'main_worker',
            'user_devices.StaticLV2SLM.blacs_workers.StaticLV2SLM_Worker',
            {
                'device_address': props.get('device_address'),
                'device_port': props.get('device_port'),
                'mock': props.get('mock', False),
            },
        )
        self.primary_worker = 'main_worker'

        self.supports_remote_value_check(False)
        self.supports_smart_programming(True)
        # self.supports_buffered_skip(True)