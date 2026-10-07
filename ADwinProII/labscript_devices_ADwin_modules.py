#####################################################################
#                                                                   #
# ADwinProII/labscript_devices_ADwin_modules.py                     #
#                                                                   #
# Copyright 2022, TU Vienna                                         #
#                                                                   #
# Implementation of the ADwin-Pro II for the labscript-suite,       #
# used in the Léonard lab for Experimental Quantum Information.     #
#                                                                   #
#####################################################################


from labscript import \
    Device, IntermediateDevice, AnalogOut, DigitalOut, AnalogIn, \
    LabscriptError, bitfield, set_passed_properties, fastflatten
from labscript_utils import dedent

import numpy as np

from . import PROCESSDELAY_TiCo, TiCo_start_cycles, module_start_index, MAX_TICO_EVENTS, PIDNO
from .ADwin_utils import ADC


    
class _ADwinCard(IntermediateDevice):
    """Dummy Class with functionality shared by all ADwin Modules"""
    def __init__(self, name, parent_device, module_address, TiCo=False, PROCESSDELAY_TiCo=None, **kwargs):
        """Creates ADwin module and if given a TiCo PseudoClock.

        Parameters
        ----------
        name : str
            Python variable name to assign to device.
        parent_device : ClockLine
            Parent ClockLine device.
        module_address : int
            Number of the module, set in the ADwin configuration.
        TiCo : bool, optional
            Set is the module is timed by its own TiCo.
        PROCESSDELAY_TiCo : int, optional
            If TiCo=True, set the process cycle time.
        **kwargs : dict, optional
            Passed to IntermediateDevice.__init__
        """
        if not type(parent_device).__name__ == "ADwinProII":
            raise LabscriptError(
                f"The ADwin module {name} needs to be created with ADwin-pro II device as parent, not {parent_device}!"
                )
        self.module_address = module_address
        self.TiCo = TiCo
        self.PROCESSDELAY = PROCESSDELAY_TiCo or parent_device.PROCESSDELAY
        self.adwin_name = parent_device.name
        if TiCo:
            self._TiCo = parent_device.add_TiCo(name,module_address,PROCESSDELAY_TiCo)
            clockline = parent_device.TiCos[name].clockline
        else:
            clockline = parent_device._clockline_T12
        super().__init__(name, clockline, **kwargs)
    
    def do_checks(self):
        pass # TODO: Are any unversal checks necessary? 

    def add_device(self, device, num_channels):
        """Calls IntermediateDevice.add_device and checks if channel is allowed.
        
        Parameters
        ----------
        device : `labscript.Device`
            Child device to add, e.g. Output
        num_channels : int
            Number of channels at the module, indexed [1,num_channels]
        """
        for existing_channel in self.child_devices:
            if device.connection==existing_channel.connection:
                raise LabscriptError(f"Cannot add '{device.name}', at channel {device.connection} already '{existing_channel.name}' is connected!")
        try:
            if not 1 <= int(device.connection) <= num_channels:
                raise LabscriptError(f"Connection of {device.name} must be between in [1,{num_channels}].")
        except ValueError:
            raise LabscriptError(
                f"Connection of {device.name} must be a number (of type string!), not {device.connection}."
                )
        IntermediateDevice.add_device(self,device)



class ADwinAnalogOut(AnalogOut):
    description = "Analog Output of ADwin-Pro II AOut-8/16"

    @set_passed_properties(property_names={"connection_table_properties": ["limits"]})
    def __init__(
            self, name, parent_device, connection, limits=(-10,10), 
            unit_conversion_class=None, unit_conversion_parameters=None, default_value=0, **kwargs
        ):
        
        AnalogOut.__init__(
                self, name, parent_device, connection, limits, 
                unit_conversion_class, unit_conversion_parameters, default_value, **kwargs
        )
        self.PID = {} # instructions for PID settings

    def init_PID(self,pid_no,P=0,I=0,D=0,limits=None):
        """Set parameters for PID once at beginning of shot.
        
        Parameters
        ----------
        pid_no : int or `AnalogIn`
            Channel of analog input for error siganl of PID feedback.
        P : float
            Proportional parameter
        I : float
            Integration parameter
        D : float
            Differential parameter
        limits : tuple of float, optional
            Limits for output voltage, defaults to output limits
        """
        if hasattr(self,"pid_no"):
            raise NotImplementedError("Only one set of PID parameters per channel is implemented.")

        if limits is None:
            # Use the Output limits if there are none specified here.
            limits = self.limits
        if limits[0]<self.limits[0] or limits[1]>self.limits[1]:
            raise LabscriptError(f"Limits of {self.name} with PID must not be larger than channel limits {self.limits}!")
        
        self.parent_device.PID_init[pid_no] = [P,I,D,*limits]


    def set_PID(self,t,pid_no,set_output=0):
        """(De-)activate PID for analog output, or change settings
        
        Parameters
        ----------
        t : float
            Time when to apply the PID settings
        pid_no : int or `AnalogIn` or None
            Channel of analog input for error siganl of PID feedback.
            If `None` PID is deactivated.
        set_value : float or "last"
            When the PID is turned on, 'set_value' is the initially chosen output value,
            'last' means that the I value from the previous PID is taken.
            When the PID is turned off, 'set_value' is programmed as the new output/target value,
            'last' means that the output from the PID loop is kept as 'set_target'.
        """
        # Error check of bounds for set_output
        if set_output!="last" and pid_no is not None:
            PID_min,PID_max = self.parent_device.PID_init[pid_no][-2:]
            if set_output<PID_min or set_output>PID_max:
                raise LabscriptError(
                    f"{self.name}: PID 'set_output={set_output}' must be within ({self.PID_min},{self.PID_max})"
                )
        # TURN OFF PID
        if pid_no is None:
            self.PID[t] = {
                "PID_channel": 0,
                "start": set_output,
            }
            # If we don't keep the PID output, set the output to the set_value
            if set_output!="last":
                self.constant(t,set_output)
        # TURN ON PID
        elif (isinstance(pid_no,AnalogIn) and isinstance(pid_no.parent_device,_ADwinCard)) \
                or isinstance(pid_no,int):
            self.PID[t] = {
                "PID_channel": pid_no,
                "start": set_output,
            }
            # TODO: Do we need scale factors for setting a PID with integer?
        else:
            msg = f"""Setting a PID is only possible with an AnalogIn object that is
                      child of an ADwin module, or the integer number of the PID."""
            raise LabscriptError(dedent(msg))

    def expand_output(self):
        """Shortcut to generate raw output from ramps without collecting all change times from pseudoclock."""
        
        # Call function to create self.times and self.instructions. We also make
        # sure the output at the end of the shot is stored in the output table.
        # self.get_change_times()
        self.times = list(self.instructions.keys())
        self.times.sort()
        times = self.times.copy() 
        if self.pseudoclock_device.stop_time not in times:
            times.append(self.pseudoclock_device.stop_time)
        self.make_timeseries(times)

        # We first have to collect all times for the ramps, then we can expand the output values.
        all_times = []
        flat_all_times_len = 0
        for i,t in enumerate(times):
            if isinstance(self.timeseries[i],dict): # Ramps are stored in dictionarys
                start = np.round(self.timeseries[i]['initial time'],9)
                end = np.round(self.timeseries[i]['end time'],9)
                ramp_times =np.linspace(
                    start,end,
                    int(np.round(self.timeseries[i]['clock rate'] * (end-start))),
                    endpoint=False
                )
                if ramp_times.size == 0:
                    raise LabscriptError(f"The ramp of {self.name} has no change times, increase the ramp duration or samplerate.")
                all_times.append(ramp_times)
                flat_all_times_len += ramp_times.size
            else: 
                all_times.append(t)
                flat_all_times_len += 1
        self.expand_timeseries(all_times,flat_all_times_len)
        # For the output table, we need all times flattened.
        self.all_times = fastflatten(all_times,float)

    def add_instruction(self,time,instruction,units=None):
        # Overwrite Output.add_instruction without limit check, becasue the value can be off-limits when this is the target value of the PID
        limits_temp = self.limits
        self.limits = (-10,10)
        super().add_instruction(time,instruction,units)
        self.limits = limits_temp

    def expand_timeseries(self,all_times,flat_all_times_len):
        # Overwrite Output.add_instruction without limit check, becasue the value can be off-limits when this is the target value of the PID
        limits_temp = self.limits
        self.limits = (-10,10)
        super().expand_timeseries(all_times,flat_all_times_len)
        self.limits = limits_temp

class ADwinAnalogIn(AnalogIn):
    """Analog Input for use with ADwin Pro II Input modules."""
    description = 'ADwin Analog Input'
    
   
    def acquire(self,label,start_time,end_time,wait_label='',storage_rate=None):
        """Command an acquisition for this input.

        Args:
            label (str): Unique label for the acquisition. Used to identify the saved trace.
            start_time (float): Time, in seconds, when the acquisition should start.
            end_time (float): Time, in seconds, when the acquisition should end.
            wait_label (str, optional): 
            scale_factor (float): Factor to scale the saved values by.
            units: Units of the input, consistent with the unit conversion class.
            storage_rate (int): Rate of data stored in the hdf5 file during shot, defaults to previously set value or ADwin processdelay.

        Returns:
            float: Duration of the acquistion, equivalent to `end_time - start_time`.
        """
        if storage_rate is not None:
            print("STORATE RATE IS IGNORED!!!")
        return super().acquire(label,start_time,end_time,wait_label='',scale_factor=None,units=None)
        
# There are 32 digital outputs on a card (DIO-32-TiCo)
class ADwinDIO32(_ADwinCard):
    description = 'ADWin-Pro II DIO32-TiCo'
    digital_dtype = np.uint32
    n_digitals = 32
    allowed_children = [DigitalOut] # DigitalIn ?

    @set_passed_properties(
        property_names={
            "connection_table_properties": ["module_address", "PROCESSDELAY_TiCo","num_DO", "num_DI", "DO_ports", "DI_ports"],
            "device_properties": [],
        }
    )
    def __init__(
            self, name, parent_device, module_address, 
            TiCo=True, PROCESSDELAY_TiCo=PROCESSDELAY_TiCo, 
            DO_ports=[], DI_ports=[], **kwargs):
        
        _ADwinCard.__init__(self, name, parent_device, module_address, TiCo, PROCESSDELAY_TiCo, **kwargs)
        
        self.DO_ports = DO_ports
        self.DI_ports = DI_ports
        if not DI_ports:
            self.DO_ports = [str(i) for i in range(1,33)]
        self.num_DO = len(self.DO_ports)
        self.num_DI = len(self.DI_ports)
    
    
    def add_device(self, device):
        _ADwinCard.add_device(self, device, self.num_DO) # TODO: DigitalIn ?


    def do_checks(self):
        # Raise an error if any DigitalOut is changed right after start, 
        # because the TiCo is only started after some cycles of the ADwin.
        times = self.digital_data["n_cycles"]
        if np.any((times>0) & (times<=TiCo_start_cycles)):
            raise LabscriptError(
                f"Digital Outputs of {self.name} cannnot change between 0 and {TiCo_start_cycles*self._TiCo.clock_resolution*1e6:.0f}µs."
            )
        # Check lenght of digital output array
        TiCo_events = self.digital_data["n_cycles"].size
        if  TiCo_events > MAX_TICO_EVENTS:
            raise LabscriptError(
                f"The number of outputs of {self.name} ({TiCo_events}) is larger than the array size in the TiCo ({MAX_TICO_EVENTS})!"
            )
        super().do_checks()


    def generate_code(self, hdf5_file):
        Device.generate_code(self, hdf5_file)
        outputs, outputs_by_clockline = self._TiCo.get_outputs_by_clockline() 
        self._TiCo.parent_device.offset_instructions_from_trigger(outputs)
        all_change_times,_ = self._TiCo.collect_change_times(outputs,outputs_by_clockline)
        outputarray = [np.zeros(len(all_change_times),dtype=self.digital_dtype)]*self.n_digitals
        
        for output in outputs:
            output.make_timeseries(all_change_times)
            channel = int(output.connection) - 1
            outputarray[channel] = np.array(output.timeseries,dtype=self.digital_dtype)
        
        bits = bitfield(outputarray, dtype=self.digital_dtype)
        
        digital_dtypes = [("n_cycles",np.int32), ("bitfield",np.uint32)]
        self.digital_data = np.empty(len(all_change_times), dtype=digital_dtypes)
        # Round to the nearest cycle rather than letting the assignment into the
        # int32 field truncate. Change times are quantised to the TiCo
        # resolution but are not exactly representable in float64, so a time
        # sitting on cycle N often comes out as N-0.0000000002. Truncating that
        # puts the event one cycle early, where it can collide with the
        # preceding event and give two rows the same n_cycles - which the TiCo
        # cannot execute, as it applies at most one bitfield per cycle.
        # Matches the analog tables, which use np.round() the same way.
        self.digital_data["n_cycles"] = np.round(np.array(all_change_times) * self._TiCo.clock_limit)
        self.digital_data["bitfield"] = bits



# Voltages are specified with a 16 bit unsigned integer, mapping the range [-10,10) volts.
# There are 8 analog outputs on a card (AOut-8/16)    
class ADwinAO8(_ADwinCard):
    description = "ADwin-Pro II-AOut-8/16 module"
    allowed_children = [ADwinAnalogOut]
    device_dtype = np.float64
    resolution_bits=16
    min_V = -10
    max_V = 10
    step_size = (max_V-min_V)/2**resolution_bits

    @set_passed_properties(
        property_names={
            "connection_table_properties": ["module_address","adwin_name","num_AO", "resolution_bits", "min_V", "max_V", "step_size", "start_index"],
            "device_properties": [],
        }
    )
    def __init__(self, name, parent_device, module_address, num_AO=8, **kwargs):
        self.num_AO = num_AO
        self.start_index = module_start_index[int(module_address)]
        self.PID_init = {}

        super().__init__(name, parent_device, module_address, **kwargs)


    def add_device(self, device):
        _ADwinCard.add_device(self, device, self.num_AO)


    def do_checks(self):
        _ADwinCard.do_checks(self)
        # Check if the sample_rate in ramp instructions is below the ADwin clockrate
        # This is necessary here, because we don't call the pseudoclock's expand_change_time()
        for output in self.child_devices:
            for instr in output.instructions.values():
                if isinstance(instr,dict) and instr["clock rate"]>self.parent_device.clock_limit:
                    rate_kHz = instr['clock rate']/1e3
                    ADwin_rate_kHz = self.parent_device.clock_limit/1e3
                    raise LabscriptError(
                        f"The ramp sample rate ({rate_kHz:.0f}kHz) of {output.name} must not be faster than ADwin ({ADwin_rate_kHz:.0f}kHz)."
                    )

            # Check limits of output, but only when PID is NOT enabled (becasue then the target can be out of limits)
            # Get all times when PID is not enabled
            PID = output.PID.copy()
            if 0 not in PID:
                # Because 'np.digitize' determines the bins, we also have to make sure t=0 is included.
                # If output.PID does not have the key t=0, then the PID is disabled in the beginning.
                PID[0] = {"PID_channel":0,"start":0}
            PID_times = np.array(list(PID.keys()))
            PID_times.sort()
            PID_off_times = []
            # For each output value, digitize gets the next highest time in  PID_times.
            # Using '-1' to get next lowest time.
            for i_out,i_PID in enumerate(np.digitize(np.round(output.all_times,6), np.round(PID_times,6))-1):
                t = PID_times[i_PID]
                if PID[t]["PID_channel"]==0:
                    # When we turn the PID off but keep the last output, we make sure that
                    #  - at least once in the end the output is (re)set, otherwise the get_final_values() in the Worker is wrong,
                    #  - don't try to also set the target value at the same time, as this would overwrite the target in the ADwin with the PID output.
                    if PID[t]["start"]=="last":
                        if i_out+1==len(output.all_times) and output.raw_output[i_out]==100_000:
                            raise LabscriptError(f"{output.name}: You must set the output at the end after turning off PID with 'last'.")
                        elif output.all_times[i_out] == round(t,9):
                            raise LabscriptError(f"{output.name}: Don't turn off PID with persitent value ('last') and also set new value.")
                    PID_off_times.append(i_out)
            PID_off_outputs = output.raw_output[PID_off_times]
            PID_off_outputs = np.round(PID_off_outputs,6)
            if np.any(PID_off_outputs < output.limits[0]) or np.any(PID_off_outputs > output.limits[1]):
                error_times = output.all_times[PID_off_times][(PID_off_outputs < output.limits[0]) < (PID_off_outputs > output.limits[1])]
                raise LabscriptError(
                    f"Limits of {output.name} (when PID is off) must be in {output.limits}, " +
                    f"you try to set ({PID_off_outputs.min()},{PID_off_outputs.max()}) " +
                    f"or turning off the PID with target value beyond limits at times {error_times}.")
            
        # Check if the PID channel is allowed
        if np.any(self.PID_table["PID_channel"] > PIDNO):
            raise LabscriptError(f"ADwin: Setting the PID channel to more than {PIDNO} is not possible!")
        if np.any(self.PID_table["PID_channel"] < 0):
            raise LabscriptError("ADwin: Setting the PID channel to less than 0 is not possible!")

    def generate_code(self,hdf5_file):
        Device.generate_code(self, hdf5_file)
        clockline = self.parent_device
        pseudoclock = clockline.parent_device

        output_dtypes = [("n_cycles",np.int32),("channel",np.int32),("value",np.int32)]
        PID_config_dtypes = [
            ("PID_channel",np.int32),("PID_P",np.float64),("PID_I",np.float64),("PID_D",np.float64),("PID_min",np.int32),("PID_max",np.int32)
        ]
        PID_table_dtypes = [
            ("n_cycles",np.int32),("AOUT_channel",np.int32),("PID_channel",np.int32),("PID_start",np.int32)
            ]
        outputs = []
        PID_table = []
        PID_config = []

        for pid_no in self.PID_init:
            P,I,D,PID_min,PID_max = self.PID_init[pid_no]
            if isinstance(pid_no,AnalogIn) and isinstance(pid_no.parent_device,_ADwinCard):
                pid_no = int(pid_no.connection) + pid_no.parent_device.start_index
            PID_min = ADC(PID_min,self.resolution_bits,self.min_V,self.max_V)
            PID_max = ADC(PID_max,self.resolution_bits,self.min_V,self.max_V)
            PID_config.append(np.array([(pid_no,P,I,D,PID_min,PID_max)], dtype=PID_config_dtypes))

        for output in sorted(self.child_devices, key=lambda dev:int(dev.connection)):
            output.expand_output()

            # Get input channels for PID, collect changed for time table and store bare channels as dataset
            if output.PID:
                # Get PID parameters
                PID_array = np.zeros(len(output.PID),dtype=PID_table_dtypes)
                PID_times = np.array(list(output.PID.keys()))
                PID_times.sort()
                PID_array["n_cycles"] = np.round(PID_times * pseudoclock.clock_limit)
                # PID_array["PID_channel"] = list(output.PID_channel.values())
                PID_array["AOUT_channel"] = int(output.connection) + self.start_index
                PID_table.append(PID_array)
                # If a PID is enabled, the set values are not the actual voltage values of the 
                # Output, but those measured at the input. If the input has a gain enabled, the
                # set values have the be scaled too, to have the PID stabilized to the right values.
                indices = np.digitize(output.all_times, PID_times)
                for i,t in enumerate(PID_times):
                    if isinstance(output.PID[t]['PID_channel'],AnalogIn):
                        PID_array["PID_channel"][i] = int(output.PID[t]['PID_channel'].connection) + output.PID[t]['PID_channel'].parent_device.start_index
                        # Sacle output by gain of PID AIn channel
                        output.raw_output[indices==i+1] *= output.PID[t]['PID_channel'].scale_factor
                    elif isinstance(output.PID[t]['PID_channel'],int):
                        PID_array["PID_channel"][i] = output.PID[t]['PID_channel']
                    if output.PID[t]["start"]=="last":
                        # When we want to use the previous value during the shot,
                        # we use a value that's out of the 16 bit ADC range to identify.
                        PID_array["PID_start"][i] = 100_000
                    else:
                        PID_array["PID_start"][i] = ADC(output.PID[t]['start'],self.resolution_bits,self.min_V,self.max_V)
                    # Error check, if PID was initialized
                    if output.PID[t]['PID_channel']!=0 and output.PID[t]['PID_channel'] not in self.PID_init:
                        raise LabscriptError(f"PID for AnalogOutput {output.name} at t={t} (pidno {PID_array['PID_channel'][i]} was never initialized!")
            # The ADwin has 16 bit output resolution, so we quantize the Voltage to the right values
            quantized_output = ADC(output.raw_output,self.resolution_bits,self.min_V,self.max_V)
            out = np.empty(quantized_output.size,dtype=output_dtypes)
            out["n_cycles"] = np.round(output.all_times * pseudoclock.clock_limit)
            out["value"] = quantized_output
            out["channel"] = int(output.connection) + self.start_index
            outputs.append(out)

        self.outputs = np.concatenate(outputs) if outputs else np.array([],dtype=output_dtypes)
        self.PID_table = np.concatenate(PID_table) if PID_table else np.array([],dtype=PID_table_dtypes)
        self.PID_config = np.concatenate(PID_config) if PID_config else np.array([],dtype=PID_config_dtypes)



# Voltages are specified with a 16 bit unsigned integer, mapping the range [-10,10) volts.
# There are 8 analog inputs on a card (AIn-F-8/16)
class ADwinAI8(_ADwinCard):
    description = "ADwin-Pro II-AIn-F-8/16 module"
    allowed_children = [AnalogIn]
    resolution_bits=16
    min_V = -10
    max_V = 10
    gain_modes = {1:0,2:1,4:2,8:3} # Scale factor 1 = Mode 0, Scale factor 2 = Mode 1, etc. 

    @set_passed_properties(
        property_names={
            "connection_table_properties": ["module_address","adwin_name","num_AI", "resolution_bits", "min_V", "max_V", "start_index"],
            "device_properties": [],
        }
    )
    def __init__(self, name, parent_device, module_address, num_AI = 8, **kwargs):
        self.num_AI = num_AI
        self.start_index = module_start_index[int(module_address)]
        _ADwinCard.__init__(self, name, parent_device, module_address, TiCo=False, **kwargs)


    def add_device(self, device):
        _ADwinCard.add_device(self, device, self.num_AI)


    def do_checks(self):
        _ADwinCard.do_checks(self)
        # TODO implement further checks if required
        if np.any(self.AIN_times["start_time"] > self.AIN_times["stop_time"]):
            raise LabscriptError(f"Start time for acquisition with Adwin {self.name} must not be later than stop time.")


    def generate_code(self,hdf5_file):
        Device.generate_code(self, hdf5_file)
        AI_group = hdf5_file.require_group(f"/devices/{self.adwin_name}/ANALOG_IN")

        clockline = self.parent_device
        pseudoclock = clockline.parent_device

        self.AIN_times = np.zeros(self.num_AI,dtype = [("start_time",np.int32),("stop_time",np.int32), ("gain_mode",np.int32)])

        # loop through all connected analog in channels and get start and end times
        for analogIn in self.child_devices:
            channel = int(analogIn.connection)
            try:
                self.AIN_times["gain_mode"][channel-1] = self.gain_modes[int(analogIn.scale_factor)]
                analogIn.set_property("scale_factor", int(analogIn.scale_factor), "connection_table_properties")
            except KeyError:
                raise LabscriptError(f"Scale factor for AnalogIn {analogIn.name} must be in {list(self.gain_modes.keys())}, not {analogIn.scale_factor}.")
            if analogIn.acquisitions:
                if len(analogIn.acquisitions)>1:
                    print(f"Warning: For channel {analogIn.name} more than one aquisition was defined, the data is also measured for all times in between!")
                # only a single acquisition is handled by adwin
                start_time = min([aqu["start_time"] for aqu in analogIn.acquisitions])
                stop_time = max([aqu["end_time"] for aqu in analogIn.acquisitions])
                # If the stop_time is later than the ADwin's, stop
                stop_time = min(stop_time,self.pseudoclock_device.stop_time)
                # convert to time steps
                start_time = np.round(start_time * pseudoclock.clock_limit)
                stop_time = np.round(stop_time * pseudoclock.clock_limit)
                self.AIN_times["start_time"][channel-1]=start_time
                self.AIN_times["stop_time"][channel-1]=stop_time
                label = ", ".join([aqu["label"] for aqu in analogIn.acquisitions])
                if not label:
                    label = analogIn.name
                AI_group.attrs[str(self.start_index+channel)] = label
