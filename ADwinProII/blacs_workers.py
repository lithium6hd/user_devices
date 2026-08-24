#####################################################################
#                                                                   #
# ADwinProII/blacs_workers.py                                       #
#                                                                   #
# Copyright 2022, TU Vienna                                         #
#                                                                   #
# Implementation of the ADwin-Pro II for the labscript-suite,       #
# used in the Léonard lab for Experimental Quantum Information.     #
#                                                                   #
#####################################################################

import time
import threading
from datetime import datetime
import sys
import numpy as np
import labscript_utils.h5_lock
import h5py
from blacs.tab_base_classes import Worker
from labscript import LabscriptError, config
from blacs.device_base_class import MODE_BUFFERED,MODE_MANUAL
from labscript_utils import properties

from .ADwin_utils import DAC,ADC
from . import CLOCK_T12, module_start_index

class ADwinProIIWorker(Worker):
    RAISE_EXCEPTIONS = 1

    def init(self):
        self.timing = None
        self.h5file = None
        self.smart_cache = {"AOUT":None, "PIDs":None, "PID_CONFIG":None, "AIN":None}
        self.smart_cache.update({DIO:None for DIO in self.DIO_ADwin_DataNo})
        self.process_number_buffered = int(self.process_buffered[-1])
        self.process_number_manual = int(self.process_manual[-1])
        if not self.mock:
            global ADwin
            # The ADwin Python module must be either found in the path, or in the standard Windows location
            try: 
                import ADwin
            except ImportError:
                sys.path.append(r"C:\ADwin\Developer\Python")
                import ADwin
            if ADwin.version < "0.18.0":
                raise ImportError("In ADwin.py version < 0.18.0 setting data with numpy arrays is not supported directly. Upgrade version or use SetData_Long(numpy.ndarray.ctypes, ...) in worker.")
            self.adw = ADwin.ADwin(self.device_no, self.RAISE_EXCEPTIONS)
        else:
            self.adw = adwDummy() # Testing without connection
        self.boot()
        self.adw.Load_Process(self.process_buffered)
        self.adw.Load_Process(self.process_manual)
        print("Loaded ADwin processes (buffered and manual).")
        # Check if the PROCESSDELAY from ADwin and labscript match
        if not self.PROCESSDELAY==self.adw.Get_Processdelay(self.process_number_buffered):
            raise LabscriptError(
                f"PROCESSDELAY from labscript ({self.PROCESSDELAY}) does not match with ADwin ({self.adw.Get_Processdelay(self.process_number_buffered)})!"
                )
            

    def boot(self):
        print(f"Booting {self.device_name}...", end="")
        if sys.platform == "win32":
            BTL = self.adw.ADwindir + "adwin12.btl"
        elif sys.platform == "linux":
            BTL = self.adw.ADwindir + "share/btl/adwin12.btl"
        self.adw.Boot(BTL)
        if self.adw.Test_Version():
            raise LabscriptError("Testing Version failed after booting ADwin")
        print("DONE")


    def wait_until_done(self):
        self.logger.debug("ADwin called check_if_done.")
        if getattr(self, 'start_time', None) is None:
            self.start_time = time.time()
        sleep = self.start_time + self.stop_time - time.time()
        if sleep>0:
            time.sleep(sleep)
        while self.adw.Get_Par(1) == 1: # Check if the 'busy' parameter in ADwin was set to 0
            time.sleep(0.0001)
        return True #self.adw.Get_Par(1) == 0


    def start_run(self):
        self.logger.debug("ADwin starting run.")
        self.start_time = time.time()
        self.adw.Start_Process(self.process_number_buffered)


    def get_final_values(self,f):
        group = f[f"devices/{self.device_name}"]
        final_values = {}
        AO_channel_map = {}
        for module_address,module in self.modules.items():
            module_props = self.module_props[module_address]
            if module_props.get("num_AO",None) is not None:
                # Set all final AO values to zero
                # This is done for two reasons: first in the current ADwin process all AO are set to 0 at the end,
                # second if some channels are not defined in labscript (and not in the h5file), we still get a front panel value of 0.
                # final_values.update({f"{module_address}/{i+1}":0 for i in range(module_props["num_AO"])})
                # For mapping (16) AO ADwin channels to format "module/channel", only needed if final lines in this function are uncommented
                idx_AO8 = module_props["start_index"]
                AO_channel_map.update({ i+idx_AO8 : f"{module_address}/{i}" for i in range(1,1+module_props["num_AO"])})

            if module_props.get("num_DO",None) is not None:
                # Confusing way to get list of 32 bools from uint32 number:
                # Get last bitfield value (newaxis because view only works with array), view it as list ar uint8, apply
                # numpy.unpackbits to get list of bits (this only works on uint8, hence view), reorder and change to bool  
                bits = np.unpackbits(group[f"DIGITAL_OUT/{module}"]["bitfield"][-1,np.newaxis].view(np.uint8)[::-1]).astype("bool")[::-1]
                # Make dict and update final_values
                final_values.update({f"{module_address}/{i}":bits[int(i)-1] for i in module_props["DO_ports"]})

        # TODO: only get final AO values, if we don't set then zo zero at end of ADwin process
        stop_time_quantized = np.round(self.stop_time * CLOCK_T12 / self.PROCESSDELAY)
        AO_data = group["ANALOG_OUT/VALUES"]
        final_indices = np.nonzero(AO_data["n_cycles"] == stop_time_quantized)[0]
        if final_indices.size>0 and final_indices[-1]+1==AO_data.shape[0]:
            # In the ANALOG_OUT table, there is a termination line. The time (cycle number) of this line
            # should be one larger than the last outputs, but sometimes this seemed to be violated. I added
            # this check to remove this line in any of those cases from trying to use it for final values.
            final_indices = final_indices[:-1]
        for idx in final_indices:
            final_values[AO_channel_map[AO_data["channel"][idx]]] = DAC(AO_data["value"][idx])
        return final_values


    def transition_to_buffered(self, device_name, h5file, initial_values, fresh):
        # print("Transition to buffered started. ", end="")
        if self.timing is not None:
            print(datetime.now(), end=" ")
            print(f"Time since last shot: {time.perf_counter()-self.timing}s.")
        else:
            print()
        self.timing = time.perf_counter()

        self.logger.debug("ADwin called transition_to_buffered.")
        self.adw.Stop_Process(self.process_number_manual)
        self.h5file = h5file
        with h5py.File(h5file, 'r') as f:
            group = f[f"devices/{device_name}"]
            # Get stop time
            self.stop_time = group.attrs["stop_time"]
            # Send stop time to ADwin
            self.adw.Set_Par(2, int(self.stop_time * CLOCK_T12 / self.PROCESSDELAY))
            # Send wait time and timeout to ADwin (default 0 if no waits)
            self.adw.Set_Par(3, int(group.attrs.get("wait_time",-1)))
            self.adw.Set_Par(5, int(group.attrs.get("wait_timeout",0)))
            # Send data to ADwin
            AOUT = group["ANALOG_OUT/VALUES"]
            if fresh or not np.array_equal(AOUT[:],self.smart_cache["AOUT"]):
                print("AOUT programmed.")
                self.smart_cache["AOUT"] = AOUT[:]
                self.adw.SetData_Long(AOUT["n_cycles"], 1, 1, AOUT.shape[0])
                self.adw.SetData_Long(AOUT["channel"], 2, 1, AOUT.shape[0])
                self.adw.SetData_Long(AOUT["value"], 3, 1, AOUT.shape[0])
            for name,module in self.DIO_ADwin_DataNo:
                if module == 50: # TODO: Fix in Adbasic
                    module = 20
                DOUT = group[f"DIGITAL_OUT/{name}"]
                if fresh or not np.array_equal(DOUT[:],self.smart_cache[name]):
                    print(f"{name} programmed.")
                    self.smart_cache[name] = DOUT[:]
                    self.adw.SetData_Long(DOUT["n_cycles"], module,   1, DOUT.shape[0])
                    self.adw.SetData_Long(DOUT["bitfield"], module+1, 1, DOUT.shape[0])
                    self.adw.Set_Par(module-1, int(DOUT.attrs.get("wait_time",-1)))
            PIDs = group["ANALOG_OUT/PID_CHANNELS"]
            if fresh or not np.array_equal(PIDs[:],self.smart_cache["PIDs"]):
                print("PIDs programmed.")
                self.smart_cache["PIDs"] = PIDs[:]
                self.adw.SetData_Long(PIDs["n_cycles"], 4, 1, PIDs.shape[0])
                self.adw.SetData_Long(PIDs["AOUT_channel"], 5, 1, PIDs.shape[0])
                self.adw.SetData_Long(PIDs["PID_channel"], 6, 1, PIDs.shape[0])
                self.adw.SetData_Long(PIDs["PID_start"], 30, 1, PIDs.shape[0])
            PID_config = group["ANALOG_OUT/PID_CONFIG"]
            if fresh or not np.array_equal(PID_config[:],self.smart_cache["PID_CONFIG"]):
                print("PID_CONFIG programmed.")
                self.smart_cache["PID_CONFIG"] = PID_config[:]
                n_PID = PID_config.shape[0]
                self.adw.Set_Par(22,n_PID)
                self.adw.SetData_Long(PID_config["PID_channel"], 24, 1, n_PID)
                self.adw.SetData_Float(PID_config["PID_P"], 25, 1, n_PID)
                self.adw.SetData_Float(PID_config["PID_I"], 26, 1, n_PID)
                self.adw.SetData_Float(PID_config["PID_D"], 27, 1, n_PID)
                self.adw.SetData_Long(PID_config["PID_min"], 28, 1, n_PID)
                self.adw.SetData_Long(PID_config["PID_max"], 29, 1, n_PID)
            AIN = group["ANALOG_IN/TIMES"]
            if fresh or not np.array_equal(AIN[:],self.smart_cache["AIN"]):
                print("AIN programmed.")
                self.smart_cache["AIN"] = AIN[:]
                self.adw.SetData_Long(AIN["start_time"], 7, 1, AIN.shape[0])
                self.adw.SetData_Long(AIN["stop_time"], 8, 1, AIN.shape[0])
                self.adw.SetData_Long(AIN["gain_mode"], 9, 1, AIN.shape[0])
            final_values = self.get_final_values(f)
        return final_values # return final values to show the right state after transition to manual
    

    def transition_to_manual(self):
        self.logger.debug("ADwin called transition_to_manual.")
        start = time.perf_counter()

        # Step 1: read everything needed from the h5 file, then release the lock.
        # The lock must NOT be held while making ADwin hardware calls — those can
        # block indefinitely, and holding the lock would deadlock any other process
        # that needs the file (e.g. lyse, BLACS queue_manager).
        with h5py.File(self.h5file, 'r') as f:
            AI_count = int(f[f"devices/{self.device_name}/ANALOG_IN"].attrs["AIN_count"])
            has_waits = f[f"devices/{self.device_name}"].attrs.get("wait_time", None) is not None
            if has_waits:
                wait_table      = f["waits"]
                wait_labels     = wait_table['label'][:]
                wait_times      = wait_table['time'][:]
                wait_timeouts   = wait_table['timeout'][:]

        # Step 2: ADwin hardware calls with a hard timeout so a hung ADwin does
        # not freeze BLACS.  The file lock is not held here.
        adwin_data = {}
        exc_box = [None]

        def _adwin_calls():
            try:
                if AI_count > 0:
                    adwin_data['AIN'] = np.ctypeslib.as_array(
                        self.adw.GetData_Long(199, 1, AI_count)
                    ).astype(np.uint16)
                if has_waits:
                    adwin_data['wait_duration'] = self.adw.Get_Par(4) / CLOCK_T12 * self.PROCESSDELAY
                # Check if the TiCo processes were running correctly
                for name, num in self.DIO_ADwin_DataNo:
                    if num == 50: # TODO: Fix in Adbasic
                        num = 20
                    if not self.adw.Get_Par(num) == 1:
                        exc_box[0] = LabscriptError(
                            f"TiCo process of module {name} was not running before at end main process."
                        )
                        return
                # Stop buffered and start manual process in ADwin
                self.adw.Stop_Process(self.process_number_buffered)
                #self.adw.Start_Process(self.process_number_manual)
            except Exception as e:
                exc_box[0] = e

        _TIMEOUT = 30  # seconds; normal ADwin calls finish in <1 s
        t = threading.Thread(target=_adwin_calls, daemon=True)
        t.start()
        t.join(_TIMEOUT)
        if t.is_alive():
            # The orphaned thread holds no file lock, so BLACS can recover safely.
            raise LabscriptError(
                f"ADwin transition_to_manual timed out after {_TIMEOUT} s — "
                "hardware communication is hung. Restart BLACS to recover."
            )
        if exc_box[0] is not None:
            raise exc_box[0]

        # Step 3: write ADwin results back to the h5 file (fast, no hardware calls).
        with h5py.File(self.h5file, 'r+') as f:
            group = f.require_group("data/traces/")
            if AI_count > 0:
                group.create_dataset(
                    "ADwinAnalogIn_DATA", compression=config.compression, data=adwin_data['AIN']
                )
            # Workload for Testing
            # stop_time = self.adw.Get_Par(2)-1
            # workload_data = np.ctypeslib.as_array(self.adw.GetData_Long(31,1,stop_time))
            # array = np.empty(stop_time, dtype=[("t",np.int32),("values",np.int32)])
            # array["t"] = np.arange(stop_time)
            # array["values"] = workload_data
            # group.create_dataset("ADwin_Workload", compression = config.compression, data = array)
            # f['devices/ADwin/ANALOG_IN'].attrs["ADwin_Workload"] = "TEST"
            if has_waits:
                wait_duration = adwin_data['wait_duration']
                dtypes = [('label', 'a256'), ('time', float), ('timeout', float),
                          ('duration', float), ('timed_out', bool)]
                data = np.empty(len(wait_labels), dtype=dtypes)
                data['label']     = wait_labels
                data['time']      = wait_times
                data['timeout']   = wait_timeouts
                data['duration']  = wait_duration
                data['timed_out'] = wait_duration > wait_timeouts
                f.create_dataset('/data/waits', data=data)

        # Delete h5file reference from worker, shot is finished
        self.h5file = None
        print(f"Time for transition_to_manual: {time.perf_counter()-start:.3f}s")
        return True


    def program_manual(self, values):
        print("ADwin Program Manual")
        if self.adw.Process_Status(self.process_number_manual) != 1: # 1 if process is running
            self.adw.Start_Process(self.process_number_manual)
        # Convert dict of frontpanel values to format for ADwin
        data = {}
        for BLACS_name,output in values.items():
            module,channel = map(int,BLACS_name.split('/'))
            if isinstance(output,dict):             # Analog output (with PID)
                data.setdefault(module,{})
                for name,value in output.items():
                    data[module].setdefault(name,np.zeros(8,dtype=np.float64))
                    data[module][name][channel-1] = value
            elif isinstance(output,bool):           # Digital output
                data.setdefault(module,0)
                data[module] += output << (channel-1)
            else:
                raise LabscriptError(f"Got unexpencted data in program_manual for channel {BLACS_name}: {output}")

        # Send data to ADwin
        for module,module_data in data.items():
            if isinstance(module_data,dict):      # Analog output 
                size = module_data["output"].size
                self.adw.SetData_Long(ADC(module_data["output"]), 97, module_start_index[module]+1, size)
                try:
                    self.adw.SetData_Long(module_data["Ch"],          98, module_start_index[module]+1, size)
                    self.adw.SetData_Double(module_data["P"],         99, module_start_index[module]+1, size)
                    self.adw.SetData_Double(module_data["I"],        100, module_start_index[module]+1, size)
                    self.adw.SetData_Double(module_data["D"],        101, module_start_index[module]+1, size)
                    self.adw.SetData_Long(ADC(module_data["min"]),   102, module_start_index[module]+1, size)
                    self.adw.SetData_Long(ADC(module_data["max"]),   103, module_start_index[module]+1, size)
                except ADwin.ADwinError:
                    # If we use the process without PIDs in manual mode, those data arrays are not initialized.
                    print("program_manual failed to send values for PIDs in manual mode.")
            else:                                   # Digital output
                if module == 5:
                    module = 2
                self.adw.Set_Par(90+module,module_data)
        # Set parameter that the output channels are updated in ADwin.
        self.adw.Set_Par(11,1)
        return values


    def get_AIN_values(self,AIN_values):
        """
        Read all analog input values from the ADwin modules if we are in manual mode.
        AIN_values : dict
            {modules_address/port: value, ...}
        """
        if self.adw.Process_Status(self.process_number_manual) != 1: # retruns 1 if process is running
            self.adw.Start_Process(self.process_number_manual)
            time.sleep(0.01) # wait some time such that the first AIN values are already measured
        for module_address,inputs in AIN_values.items():
            if module_address == 2: # TODO: Fix in Adbasic
                module_address = 3
            elif module_address == 6:
                module_address = 4
            values = self.adw.GetData_Long(90+int(module_address), 1, len(inputs))
            values = DAC(np.ctypeslib.as_array(values))
            for port in inputs:
                inputs[port] = values[port-1]
        return AIN_values


    def shutdown(self):
        self.adw.Stop_Process(self.process_number_buffered)
        self.adw.Stop_Process(self.process_number_manual)
        return True


    def abort_transition_to_buffered(self):
        self.adw.Stop_Process(self.process_number_buffered)
        self.adw.Start_Process(self.process_number_manual)
        return True
    

    def abort_buffered(self):
        return self.abort_transition_to_buffered()


    # Functions for manual interaction from BLACS GUI
    def get_workload(self):
        workload_shot = self.adw.Get_Par(13) / (self.adw.Get_Par(2)-1) / self.PROCESSDELAY
        return workload_shot, self.adw.Free_Mem(5), self.adw.Free_Mem(6)


    def load_process(self,process,process_type):
        print("Load process",int(process[-1]))
        if process_type == "buffered":
            self.adw.Clear_Process(self.process_number_buffered)
            self.adw.Load_Process(process)
            self.process_number_buffered = int(process[-1])
        elif process_type == "manual":
            self.adw.Clear_Process(self.process_number_manual)
            self.adw.Load_Process(process)
            self.process_number_manual = int(process[-1])
        if self.process_number_manual == self.process_number_buffered:
            raise LabscriptError("Having two processes with the same process number on the ADwin is not possible!")
    
    
    def start_process(self,process):
        print("Start process",int(process[-1]))
        self.adw.Start_Process(int(process[-1]))
    
    
    def stop_process(self,process):
        print("Stop process",int(process[-1]))
        self.adw.Stop_Process(int(process[-1]))
 


# For testing without hardware connection to ADwin, 
# we can use a dummy adw class with the needed functions
class adwDummy(object):
    """ADwin dummy for testing without hardware commection."""
    ADwindir = ""
    def Test_Version(self):
        return 0
    def Load_Process(self,*args):
        pass
    def Start_Process(self,*args):
        pass
    def Stop_Process(self,*args):
        pass
    def Clear_Process(self,*args):
        pass
    def Process_Status(self,*args):
        pass
    def Boot(self,*args):
        pass
    def Workload(self):
        return 0
    def Free_Mem(self,*args):
        return 0
    def Get_Par(self, no):
        if no==1:
            return 0
        elif no==2:
            return 2
        else:
            return 1
    def Get_Processdelay(self,*args):
        from . import PROCESSDELAY_T12
        return PROCESSDELAY_T12
    def GetData_Long(self,no,start,size):
        import numpy
        vals = ADC(numpy.zeros(size)+10)
        return numpy.ctypeslib.as_ctypes(vals)
    def Set_Par(self,*args):
        pass
    def Set_FPar(self,*args):
        pass
    def SetData_Long(self,*args):
        pass
    def SetData_Float(self,*args):
        pass
    def SetData_Double(self,*args):
        pass
