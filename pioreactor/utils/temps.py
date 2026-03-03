# -*- coding: utf-8 -*-
from __future__ import annotations
import asyncio

from adafruit_bus_device.i2c_device import I2CDevice
from busio import I2C  # type: ignore

from pioreactor import hardware


class TMP1075:
    """
    Driver for the TI TMP1075 temperature sensor.
    See datasheet: http://www.ti.com/lit/ds/symlink/tmp1075.pdf

    """

    TEMP_REGISTER = bytearray([0x00])
    # CONFIG_REGISTER = bytearray([0x01])

    def __init__(self, address: int = 0x4F):
        """Initialize the TMP1075 driver.
        
        This version doesn't raise an exception if the device is not connected.
        Instead, it sets a flag that's checked during temperature readings.
        """
        from pioreactor.hardware import SCL, SDA
        
        self.address = address
        self.connected = False
        self.i2c = None
        self.comm_port = None
        
        try:
            self.comm_port = I2C(SCL, SDA)
            # Check if the device is present before trying to create a device
            self.i2c = I2CDevice(self.comm_port, address, probe=True)
            
            # Try an actual read to confirm connectivity
            test_buf = bytearray(2)
            self.i2c.write_then_readinto(self.TEMP_REGISTER, test_buf)
            
            self.connected = True
        except (ValueError, OSError):
            # Device not found or error reading - will return None for temperature readings
            self.connected = False
            # Don't raise an exception here, just mark as disconnected
            pass

    def get_temperature(self) -> float:
        """Read temperature from the sensor.
        
        If the sensor is not connected, raises OSError.
        """
        if not self.connected or self.i2c is None:
            raise OSError(f"Temperature sensor at address 0x{self.address:02x} is not connected")
            
        b = bytearray(2)
        # try:
        self.i2c.write_then_readinto(self.TEMP_REGISTER, b)
        return ((b[0] << 4) + (b[1] >> 4)) * 0.0625
        # except OSError as e:
            # # If we get an error during reading, mark the device as disconnected
            # self.connected = False
            # asyncio.run_coroutine_threadsafe(self.__init__(self.address), self.)  #TODO schedule reconnection
            # raise OSError(f"Error reading from temperature sensor at address 0x{self.address:02x}: {str(e)}")
            

    @property
    def temperature(self) -> float:
        """alias for get_temperature"""
        return self.get_temperature()



"""MCP9600 Driver."""
from i2cdevice import Device, Register, BitField, _int_to_bytes
from i2cdevice.adapter import LookupAdapter, Adapter
import struct

__version__ = '0.0.4'

CHIP_ID = 0x41
I2C_ADDRESSES = list(range(0x60, 0x68))
I2C_ADDRESS_DEFAULT = 0x66
I2C_ADDRESS_ALTERNATE = 0x67


class RevisionAdapter(Adapter):
    def _decode(self, value):
        major = (value & 0xF0) >> 4
        minor = (value * 0x0F)
        return major + (minor / 10.0)


class TemperatureAdapter(Adapter):
    def _decode(self, value):
        b = _int_to_bytes(value, 2)
        v = struct.unpack('>h', b)[0]
        return v / 16.0


class AlertLimitAdapter(Adapter):
    def _decode(self, value):
        v = struct.unpack('>h', _int_to_bytes(value, 2))[0]
        return v / 16.0

    def _encode(self, value):
        v = int(value * 4) << 2
        v = struct.pack('>h', v)
        try:
            v = v[0] << 8 | v[1]
        except TypeError:
            v = ord(v[0]) << 8 | ord(v[1])
        return v


class MCP9600:
    def __init__(self, i2c_addr=I2C_ADDRESS_DEFAULT, i2c_dev=None):
        self._is_setup = False
        self._i2c_addr = i2c_addr
        self._i2c_dev = i2c_dev
        self._mcp9600 = Device(I2C_ADDRESSES, i2c_dev=self._i2c_dev, bit_width=8, registers=(
            Register('HOT_JUNCTION', 0x00, fields=(
                BitField('temperature', 0xFFFF, adapter=TemperatureAdapter()),
            ), bit_width=16),
            Register('DELTA', 0x01, fields=(
                BitField('value', 0xFFFF, adapter=TemperatureAdapter()),
            ), bit_width=16),
            Register('COLD_JUNCTION', 0x02, fields=(
                BitField('temperature', 0x1FFF, adapter=TemperatureAdapter()),
            ), bit_width=16),
            Register('RAW_DATA', 0x03, fields=(
                BitField('adc', 0xFFFFFF),
            ), bit_width=24),
            Register('STATUS', 0x04, fields=(
                BitField('burst_complete', 0b10000000),
                BitField('updated', 0b01000000),
                BitField('input_range', 0b00010000),
                BitField('alert_4', 0b00001000),
                BitField('alert_3', 0b00000100),
                BitField('alert_2', 0b00000010),
                BitField('alert_1', 0b00000001)
            )),
            Register('THERMOCOUPLE_CONFIG', 0x05, fields=(
                BitField('type_select', 0b01110000, adapter=LookupAdapter({
                    'K': 0b000,
                    'J': 0b001,
                    'T': 0b010,
                    'N': 0b011,
                    'S': 0b100,
                    'E': 0b101,
                    'B': 0b110,
                    'R': 0b111
                })),
                BitField('filter_coefficients', 0b00000111)
            )),
            Register('DEVICE_CONFIG', 0x06, fields=(
                BitField('cold_junction_resolution', 0b10000000, adapter=LookupAdapter({
                    0.0625: 0b0,
                    0.25: 0b1
                })),
                BitField('adc_resolution', 0b01100000, adapter=LookupAdapter({
                    18: 0b00,
                    16: 0b01,
                    14: 0b10,
                    12: 0b11
                })),
                BitField('burst_mode_samples', 0b00011100, adapter=LookupAdapter({
                    1: 0b000,
                    2: 0b001,
                    4: 0b010,
                    8: 0b011,
                    16: 0b100,
                    32: 0b101,
                    64: 0b110,
                    128: 0b111
                })),
                BitField('shutdown_modes', 0b00000011, adapter=LookupAdapter({
                    'Normal': 0b00,
                    'Shutdown': 0b01,
                    'Burst': 0b10
                }))
            )),
            Register('ALERT1_CONFIG', 0x08, fields=(
                BitField('clear_interrupt', 0b10000000),
                BitField('monitor_junction', 0b00010000),  # 1 Cold Junction, 0 Thermocouple
                BitField('rise_fall', 0b00001000),         # 1 rising, 0 cooling
                BitField('state', 0b00000100),             # 1 active high, 0 active low
                BitField('mode', 0b00000010),              # 1 interrupt mode, 0 comparator mode
                BitField('enable', 0b00000001)             # 1 enable, 0 disable
            )),
            Register('ALERT2_CONFIG', 0x09, fields=(
                BitField('clear_interrupt', 0b10000000),
                BitField('monitor_junction', 0b00010000),  # 1 Cold Junction, 0 Thermocouple
                BitField('rise_fall', 0b00001000),         # 1 rising, 0 cooling
                BitField('state', 0b00000100),             # 1 active high, 0 active low
                BitField('mode', 0b00000010),              # 1 interrupt mode, 0 comparator mode
                BitField('enable', 0b00000001)             # 1 enable, 0 disable
            )),
            Register('ALERT3_CONFIG', 0x0A, fields=(
                BitField('clear_interrupt', 0b10000000),
                BitField('monitor_junction', 0b00010000),  # 1 Cold Junction, 0 Thermocouple
                BitField('rise_fall', 0b00001000),         # 1 rising, 0 cooling
                BitField('state', 0b00000100),             # 1 active high, 0 active low
                BitField('mode', 0b00000010),              # 1 interrupt mode, 0 comparator mode
                BitField('enable', 0b00000001)             # 1 enable, 0 disable
            )),
            Register('ALERT4_CONFIG', 0x0B, fields=(
                BitField('clear_interrupt', 0b10000000),
                BitField('monitor_junction', 0b00010000),  # 1 Cold Junction, 0 Thermocouple
                BitField('rise_fall', 0b00001000),         # 1 rising, 0 cooling
                BitField('state', 0b00000100),             # 1 active high, 0 active low
                BitField('mode', 0b00000010),              # 1 interrupt mode, 0 comparator mode
                BitField('enable', 0b00000001)             # 1 enable, 0 disable
            )),
            Register('ALERT1_HYSTERESIS', 0x0C, fields=(
                BitField('value', 0xFF),
            )),
            Register('ALERT2_HYSTERESIS', 0x0D, fields=(
                BitField('value', 0xFF),
            )),
            Register('ALERT3_HYSTERESIS', 0x0E, fields=(
                BitField('value', 0xFF),
            )),
            Register('ALERT4_HYSTERESIS', 0x0F, fields=(
                BitField('value', 0xFF),
            )),
            Register('ALERT1_LIMIT', 0x10, fields=(
                BitField('value', 0xFFFF, adapter=AlertLimitAdapter()),
            ), bit_width=16),
            Register('ALERT2_LIMIT', 0x11, fields=(
                BitField('value', 0xFFFF, adapter=AlertLimitAdapter()),
            ), bit_width=16),
            Register('ALERT3_LIMIT', 0x12, fields=(
                BitField('value', 0xFFFF, adapter=AlertLimitAdapter()),
            ), bit_width=16),
            Register('ALERT4_LIMIT', 0x13, fields=(
                BitField('value', 0xFFFF, adapter=AlertLimitAdapter()),
            ), bit_width=16),
            Register('CHIP_ID', 0x20, fields=(
                BitField('id', 0xFF00),
                BitField('revision', 0x00FF, adapter=RevisionAdapter())
            ), bit_width=16)
        ))

        self.alert_registers = [
            'ALERT1_CONFIG',
            'ALERT2_CONFIG',
            'ALERT3_CONFIG',
            'ALERT4_CONFIG'
        ]
        self.alert_limits = [
            'ALERT1_LIMIT',
            'ALERT2_LIMIT',
            'ALERT3_LIMIT',
            'ALERT4_LIMIT'
        ]
        self.alert_hysteresis = [
            'ALERT1_HYSTERESIS',
            'ALERT2_HYSTERESIS',
            'ALERT3_HYSTERESIS',
            'ALERT4_HYSTERESIS'
        ]

        self._mcp9600.select_address(self._i2c_addr)

        try:
            chip = self._mcp9600.get('CHIP_ID')
            if chip.id != CHIP_ID:
                raise RuntimeError("Unable to find mcp9600 on 0x{:02x}, CHIP_ID returned {:02x}".format(self._i2c_addr, chip.id))
        except IOError:
            raise RuntimeError("Unable to find mcp9600 on 0x{:02x}, IOError".format(self._i2c_addr))

    def setup(self):
        pass

    def set_thermocouple_type(self, thermocouple_type):
        """Set the type of thermocouple connected to the MCP9600.

        :param thermocouple_type: One of 'K', 'J', 'T', 'N', 'S', 'E', 'B' or 'R'

        """
        self._mcp9600.set('THERMOCOUPLE_CONFIG', type_select=thermocouple_type)

    def get_thermocouple_type(self):
        """Get the type of thermocouple connected to the MCP9600.

        Returns one of 'K', 'J', 'T', 'N', 'S', 'E', 'B' or 'R'

        """
        return self._mcp9600.get('THERMOCOUPLE_CONFIG').type_select

    def get_hot_junction_temperature(self):
        """Return the temperature measured by the attached thermocouple."""
        return self._mcp9600.get('HOT_JUNCTION').temperature

    def get_cold_junction_temperature(self):
        """Return the temperature measured by the onboard sensor."""
        return self._mcp9600.get('COLD_JUNCTION').temperature

    def get_temperature_delta(self):
        """Return the difference between hot and cold junction temperatures."""
        return self._mcp9600.get('DELTA').value

    def check_alerts(self):
        """Check status flags of all alert registers."""
        status = self._mcp9600.get('STATUS')
        return status.alert_1, status.alert_2, status.alert_3, status.alert_4

    def clear_alert(self, index):
        """Clear the interrupt flag on an alert slot.

        :param index: Index of alert to clear, from 1 to 4

        """
        self._mcp9600.set(self.alert_registers[index - 1], clear_interrupt=1)

    def get_alert_hysteresis(self, index):
        alert_hysteresis = self.alert_hysteresis[index - 1]
        return self._mcp9600.get(alert_hysteresis).value

    def get_alert_limit(self, index):
        alert_limit = self.alert_limits[index - 1]
        return self._mcp9600.get(alert_limit).value

    def configure_alert(self, index, limit=None, hysteresis=None, clear_interrupt=True, monitor_junction=0, rise_fall=1, state=1, mode=1, enable=False):
        """Set up one of the 4 alert slots.

        :param index: Index of alert to set, from 1 to 4
        :param limit: Temperature limit
        :param hysteresis: Temperature hysteresis
        :param clear_interrupt: Whether to clear the interrupt flag
        :param monitor_junction: Which junction to monitor: 0 = HOT, 1 = COLD
        :param rise_fall: Monitor for 1=rising or 0=falling temperature
        :param state: Active 1=high or 0=low
        :param mode: 1=Interrupt mode, must clear interrupt to de-assert, 0=Comparator mode
        :param enable: True=Enabled, False=Disabled

        """
        alert_register = self.alert_registers[index - 1]

        if limit is not None:
            alert_limit = self.alert_limits[index - 1]
            self._mcp9600.set(alert_limit, value=limit)

        if hysteresis is not None:
            alert_hysteresis = self.alert_hysteresis[index - 1]
            self._mcp9600.set(alert_hysteresis, value=hysteresis)

        self._mcp9600.set(alert_register,
                          clear_interrupt=1 if clear_interrupt else 0,
                          monitor_junction=monitor_junction,
                          rise_fall=rise_fall,
                          state=state,
                          mode=mode,
                          enable=1 if enable else 0)


"""ADS1115 Driver for Ratiometric NTC Thermistor Measurement."""
import time
import math
import struct
import threading
from typing import Optional


class ADS1115_Thermistor:
    """
    Driver for ADS1115 ADC with ratiometric NTC thermistor measurement.
    
    Designed for use with Adafruit ADS1115 breakout board.
    See datasheet: https://www.ti.com/lit/ds/symlink/ads1115.pdf
    
    Hardware Configuration:
    - A0: Connected to junction of reference resistor and thermistor
    - A1: Connected to VDD (voltage reference) via jumper
    - GND: Connected to thermistor ground
    - VDD: Connected to reference resistor and A1 jumper
    
    Circuit:
    VDD ---[R_ref = 10kΩ]--- A0 ---[NTC Thermistor]--- GND
                              |
                             A1 (measures VDD via jumper)
    """
    
    # ADS1115 Register Addresses
    REG_CONVERSION = 0x00
    REG_CONFIG = 0x01
    
    # Configuration Register Bits
    # Operational status/single-shot conversion start
    OS_SINGLE = 0x8000
    
    # Input multiplexer configuration (differential and single-ended)
    MUX_AIN0_GND = 0x4000  # A0 to GND
    MUX_AIN1_GND = 0x5000  # A1 to GND
    MUX_AIN2_GND = 0x6000  # A2 to GND
    MUX_AIN3_GND = 0x7000  # A3 to GND
    
    # Programmable gain amplifier configuration
    PGA_4_096V = 0x0200  # ±4.096V range
    
    # Device operating mode
    MODE_SINGLE = 0x0100  # Single-shot mode
    
    # Data rate
    DR_16SPS = 0x0020    # 16 samples per second (~62.5ms per conversion)
    DR_128SPS = 0x0080   # 128 samples per second (~7.8ms per conversion)
    
    # Comparator mode (not used, but set to default)
    COMP_MODE_TRAD = 0x0000
    COMP_POL_LOW = 0x0000
    COMP_LAT_NONE = 0x0000
    COMP_QUE_DISABLE = 0x0003
    
    # Steinhart-Hart coefficients must be set via set_thermistor_parameters()
    # after initialization for accurate temperature readings
    STEINHART_A = None
    STEINHART_B = None
    STEINHART_C = None

    # Class-level reentrant lock shared across all instances to prevent concurrent ADC access
    # This is critical because multiple sensor instances may share the same physical ADC chip
    # RLock allows the same thread to acquire the lock multiple times (for nested calls)
    _adc_lock = threading.RLock()

    def __init__(self,
                 address: int = 0x48,
                 thermistor_channel: int = 0,
                 ref_channel: int = 2,
                 r_ref: float = 10000.0,
                 pga_gain: int = PGA_4_096V,
                 data_rate: int = DR_128SPS,
                 mux_channel: int | None = None):
        """
        Initialize the ADS1115 thermistor driver.

        Args:
            address: I2C address of ADS1115 (default 0x48)
                    Can be 0x48, 0x49, 0x4A, or 0x4B via ADDR pin jumpers
            thermistor_channel: ADS1115 channel connected to thermistor (0-3, default 0)
            ref_channel: ADS1115 channel connected to VDD reference (0-3, default 2)
            r_ref: Reference resistor value in ohms (default 10000.0)
            pga_gain: PGA gain setting (default PGA_4_096V)
            data_rate: Sample rate setting (default DR_128SPS for 10K sensors,
                      use DR_16SPS for 100K sensors with high source impedance)
            mux_channel: PCA9546 mux channel this device is behind (None if on main bus)

        Hardware Configuration:
            Water sensor (10K NTC):  A0 (thermistor_channel=0), A2 (ref_channel=2, shared)
                                     Use DR_128SPS (fast, low impedance)
            Heater sensor (100K NTC): A1 (thermistor_channel=1), A2 (ref_channel=2, shared)
                                      Use DR_16SPS (slow, high impedance needs settling time)

        Note: After initialization, set the calibrated Steinhart-Hart coefficients using
              set_thermistor_parameters() method for accurate temperature readings.
        """
        from pioreactor.hardware import SCL, SDA

        self.address = address
        self.thermistor_channel = thermistor_channel
        self.ref_channel = ref_channel
        self.r_ref = r_ref
        self.pga_gain = pga_gain
        self.data_rate = data_rate
        self.mux_channel = mux_channel
        self.connected = False
        self.i2c = None
        self.comm_port = None
        
        # Map channel numbers to MUX config values
        self.channel_mux_map = {
            0: self.MUX_AIN0_GND,
            1: self.MUX_AIN1_GND,
            2: self.MUX_AIN2_GND,
            3: self.MUX_AIN3_GND,
        }
        
        # Voltage range based on PGA setting (in volts)
        self.pga_ranges = {
            0x0000: 6.144,   # ±6.144V
            0x0200: 4.096,   # ±4.096V
            0x0400: 2.048,   # ±2.048V
            0x0600: 1.024,   # ±1.024V
            0x0800: 0.512,   # ±0.512V
            0x0A00: 0.256,   # ±0.256V
        }
        self.voltage_range = self.pga_ranges.get(pga_gain, 4.096)
        
        # Calculate appropriate sleep time based on data rate
        # Add 20% margin for safety
        self.conversion_time = self._calculate_conversion_time(data_rate)
        
        try:
            self.comm_port = I2C(SCL, SDA)

            # If behind a PCA9546 mux, create a device for the mux and select the channel
            if self.mux_channel is not None:
                from pioreactor.hardware import PCA9546_ADDR
                self.mux_device = I2CDevice(self.comm_port, PCA9546_ADDR)
                self.mux_device.write(bytes([1 << self.mux_channel]))
            else:
                self.mux_device = None

            # Now probe the ADS1115 (reachable because mux channel is already selected)
            self.i2c = I2CDevice(self.comm_port, address, probe=True)

            # Test read config register to confirm connectivity (doesn't require conversion)
            test_buf = bytearray(2)
            self.i2c.write_then_readinto(bytearray([self.REG_CONFIG]), test_buf)

            self.connected = True
        except (ValueError, OSError):
            self.connected = False
    
    def _calculate_conversion_time(self, data_rate: int) -> float:
        """
        Calculate conversion time in seconds based on data rate setting.
        
        Args:
            data_rate: Data rate configuration bits
            
        Returns:
            Conversion time in seconds with 20% safety margin
        """
        # Data rate to SPS mapping
        rate_map = {
            0x0000: 8,      # 8 SPS
            0x0020: 16,     # 16 SPS
            0x0040: 32,     # 32 SPS
            0x0060: 64,     # 64 SPS
            0x0080: 128,    # 128 SPS
            0x00A0: 250,    # 250 SPS
            0x00C0: 475,    # 475 SPS
            0x00E0: 860,    # 860 SPS
        }
        
        sps = rate_map.get(data_rate, 128)
        # Add 20% margin to base conversion time
        return (1.0 / sps) * 1.2

    def _select_mux(self):
        """Re-select the PCA9546 mux channel for this device."""
        if self.mux_channel is not None:
            self.mux_device.write(bytes([1 << self.mux_channel]))

    def _write_config(self, mux_config: int) -> None:
        """
        Write configuration to ADS1115.
        
        Args:
            mux_config: Multiplexer configuration bits
        """
        if not self.connected or self.i2c is None:
            raise OSError(f"ADS1115 at address 0x{self.address:02x} is not connected")
        
        # Build configuration word
        config = (self.OS_SINGLE |      # Start single conversion
                 mux_config |           # Input multiplexer config
                 self.pga_gain |        # PGA gain
                 self.MODE_SINGLE |     # Single-shot mode
                 self.data_rate |       # Data rate (now instance-specific)
                 self.COMP_MODE_TRAD |  # Comparator mode (traditional)
                 self.COMP_POL_LOW |    # Comparator polarity (active low)
                 self.COMP_LAT_NONE |   # Non-latching comparator
                 self.COMP_QUE_DISABLE) # Disable comparator queue
        
        # Pack configuration as big-endian 16-bit value
        config_bytes = struct.pack('>H', config)
        
        # Write to config register
        write_buf = bytearray([self.REG_CONFIG]) + config_bytes
        self.i2c.write(write_buf)
    
    def _read_conversion(self) -> int:
        """
        Read the conversion register value.
        
        Returns:
            Raw 16-bit ADC value (signed)
        """
        if not self.connected or self.i2c is None:
            raise OSError(f"ADS1115 at address 0x{self.address:02x} is not connected")
        
        result_buf = bytearray(2)
        self.i2c.write_then_readinto(bytearray([self.REG_CONVERSION]), result_buf)
        
        # Unpack as signed 16-bit big-endian
        adc_value = struct.unpack('>h', result_buf)[0]
        
        return adc_value
    
    def _read_adc(self, mux_config: int) -> int:
        """
        Read ADC value from specified input with proper settling.
        
        CRITICAL: After MUX channel change, the first conversion result may be 
        invalid due to incomplete settling of the sampling capacitor, especially 
        with high source impedance (e.g., 100kΩ dividers). This function performs 
        a dummy read and discards it to ensure the returned value is accurate.
        
        Args:
            mux_config: Multiplexer configuration (which input to read)
            
        Returns:
            Raw 16-bit ADC value (signed)
        """
        if not self.connected or self.i2c is None:
            raise OSError(f"ADS1115 at address 0x{self.address:02x} is not connected")
        
        try:
            # Configure and start first conversion
            self._write_config(mux_config)
            
            # Wait for first conversion to complete
            time.sleep(self.conversion_time)
            
            # DISCARD first reading (may contain stale data from previous channel)
            self._read_conversion()
            
            # Start second conversion (sampling cap now properly settled)
            self._write_config(mux_config)
            
            # Wait for second conversion to complete
            time.sleep(self.conversion_time)
            
            # Read and return the valid conversion result
            adc_value = self._read_conversion()
            
            return adc_value
            
        except (OSError, RuntimeError) as e:
            self.connected = False
            raise OSError(f"Error reading from ADS1115 at address 0x{self.address:02x}: {str(e)}")
    
    def _adc_to_voltage(self, adc_value: int) -> float:
        """
        Convert raw ADC value to voltage.
        
        Args:
            adc_value: Raw 16-bit ADC value
            
        Returns:
            Voltage in volts
        """
        # ADS1115 is 16-bit, ranging from -32768 to 32767
        # Voltage = (ADC_value / 32768) * voltage_range
        return (adc_value / 32768.0) * self.voltage_range
    
    def _read_voltages(self) -> tuple[float, float]:
        """
        Read voltages from thermistor and reference channels.

        Uses a class-level lock to prevent concurrent ADC access when multiple
        sensor instances share the same physical ADC chip. This is critical to
        prevent race conditions where one sensor's channel switch corrupts
        another sensor's reading.

        Returns:
            Tuple of (thermistor_voltage, reference_voltage)
        """
        # Acquire lock to ensure atomic read of both channels
        # This prevents another sensor from switching the mux mid-read
        with self._adc_lock:
            # Select PCA9546 mux channel if this device is behind a mux
            if self.mux_channel is not None:
                self._select_mux()

            # Read thermistor channel (A0 or A1)
            mux_therm = self.channel_mux_map[self.thermistor_channel]
            adc_therm = self._read_adc(mux_therm)
            v_thermistor = self._adc_to_voltage(adc_therm)

            # Read reference channel (A2, shared)
            mux_ref = self.channel_mux_map[self.ref_channel]
            adc_ref = self._read_adc(mux_ref)
            v_ref = self._adc_to_voltage(adc_ref)

            return v_thermistor, v_ref
    
    def get_resistance(self) -> float:
        """
        Calculate the thermistor resistance using ratiometric measurement.
        
        Returns:
            Resistance in ohms
        """
        v_thermistor, v_ref = self._read_voltages()
        
        # Validation
        if v_thermistor <= 0 or v_ref <= 0:
            raise ValueError(f"Invalid voltage readings: V_therm={v_thermistor:.3f}V, V_ref={v_ref:.3f}V")
        
        if v_thermistor >= v_ref:
            raise ValueError(f"Thermistor voltage ({v_thermistor:.3f}V) >= reference voltage ({v_ref:.3f}V). Check wiring.")
        
        # Ratiometric calculation: R_thermistor = R_ref * (V_thermistor / (V_ref - V_thermistor))
        voltage_ratio = v_thermistor / (v_ref - v_thermistor)
        r_thermistor = self.r_ref * voltage_ratio
        
        return r_thermistor
    
    def _resistance_to_temperature_steinhart(self, resistance: float) -> float:
        """
        Convert resistance to temperature using Steinhart-Hart equation.
        
        Args:
            resistance: Thermistor resistance in ohms
            
        Returns:
            Temperature in Celsius
            
        Raises:
            ValueError: If Steinhart-Hart coefficients are not set or resistance is invalid
        """
        if self.STEINHART_A is None or self.STEINHART_B is None or self.STEINHART_C is None:
            raise ValueError(
                "Steinhart-Hart coefficients not set. Call set_thermistor_parameters() "
                "with steinhart_a, steinhart_b, and steinhart_c before reading temperature."
            )
        
        if resistance <= 0:
            raise ValueError("Resistance must be positive")
        
        ln_r = math.log(resistance)
        
        # Steinhart-Hart equation: 1/T = A + B*ln(R) + C*ln(R)^3
        temp_k = 1.0 / (self.STEINHART_A + 
                        self.STEINHART_B * ln_r + 
                        self.STEINHART_C * (ln_r ** 3))
        
        temp_c = temp_k - 273.15
        return temp_c
    
    def _resistance_to_temperature_beta(self, resistance: float) -> float:
        """
        Convert resistance to temperature using Beta equation (simplified).
        DEPRECATED: Only Steinhart-Hart method is supported. This method is kept
        for backward compatibility but should not be used.
        
        Args:
            resistance: Thermistor resistance in ohms
            
        Returns:
            Temperature in Celsius
        """
        raise NotImplementedError("Beta equation not supported. Use Steinhart-Hart coefficients instead.")
    
    def _read_thermistor_voltage_batch(self, count: int) -> list[float]:
        """
        Read multiple thermistor voltage samples without channel switching.

        This is more efficient than calling _read_voltages() multiple times because
        it stays on the thermistor channel and avoids repeated switching to the
        reference channel.

        Args:
            count: Number of thermistor voltage samples to read

        Returns:
            List of thermistor voltages
        """
        mux_therm = self.channel_mux_map[self.thermistor_channel]
        voltages = []

        for i in range(count):
            adc_value = self._read_adc(mux_therm)
            v_thermistor = self._adc_to_voltage(adc_value)
            voltages.append(v_thermistor)

            # Small delay between samples (except for last one)
            if i < count - 1:
                time.sleep(0.01)

        return voltages

    def get_temperature(self, samples: int = 1) -> float:
        """
        Read temperature from the thermistor using Steinhart-Hart equation.

        For better accuracy, this method now averages resistance values before
        converting to temperature, rather than averaging temperature values.
        This is mathematically more correct because the Steinhart-Hart equation
        is nonlinear (contains ln(R) and ln(R)³ terms).

        OPTIMIZATION: When taking multiple samples, this method batches thermistor
        voltage readings to minimize channel switching, then reads the reference
        voltage separately. This significantly reduces ADC settling time.

        Args:
            samples: Number of samples to average (default 1)

        Returns:
            Temperature in Celsius (rounded to 2 decimal places)
        """
        if not self.connected:
            raise OSError(f"ADS1115 at address 0x{self.address:02x} is not connected")

        # Acquire lock for the entire batch operation
        with self._adc_lock:
            # Select PCA9546 mux channel if this device is behind a mux
            if self.mux_channel is not None:
                self._select_mux()

            if samples == 1:
                # Fast path: single sample, use original method
                resistance = self.get_resistance()
                temp = self._resistance_to_temperature_steinhart(resistance)
                return round(temp, 2)

            # Batch read thermistor voltages (all samples, no channel switching)
            v_thermistors = self._read_thermistor_voltage_batch(samples)

            # Read reference voltage once (it's stable, doesn't need averaging)
            mux_ref = self.channel_mux_map[self.ref_channel]
            adc_ref = self._read_adc(mux_ref)
            v_ref = self._adc_to_voltage(adc_ref)

        # Calculate resistances from batched voltages
        resistances = []
        for v_thermistor in v_thermistors:
            # Validation
            if v_thermistor <= 0 or v_ref <= 0:
                raise ValueError(f"Invalid voltage readings: V_therm={v_thermistor:.3f}V, V_ref={v_ref:.3f}V")

            if v_thermistor >= v_ref:
                raise ValueError(f"Thermistor voltage ({v_thermistor:.3f}V) >= reference voltage ({v_ref:.3f}V). Check wiring.")

            # Ratiometric calculation: R_thermistor = R_ref * (V_thermistor / (V_ref - V_thermistor))
            voltage_ratio = v_thermistor / (v_ref - v_thermistor)
            r_thermistor = self.r_ref * voltage_ratio
            resistances.append(r_thermistor)

        # Average the resistances (more accurate than averaging temperatures)
        r_avg = sum(resistances) / len(resistances)

        # Convert the averaged resistance to temperature
        temp = self._resistance_to_temperature_steinhart(r_avg)

        return round(temp, 2)
    
    @property
    def temperature(self) -> float:
        """Alias for get_temperature()"""
        return self.get_temperature()
    
    def set_thermistor_parameters(self, 
                                   steinhart_a: Optional[float] = None,
                                   steinhart_b: Optional[float] = None,
                                   steinhart_c: Optional[float] = None):
        """
        Update Steinhart-Hart coefficients for temperature calculation.
        These should be determined through calibration for your specific thermistor.
        
        Args:
            steinhart_a: Steinhart-Hart A coefficient
            steinhart_b: Steinhart-Hart B coefficient
            steinhart_c: Steinhart-Hart C coefficient
            
        Example:
            # For 10K water sensor (calibrated values)
            sensor.set_thermistor_parameters(
                steinhart_a=0.0007904962,
                steinhart_b=0.0002849790,
                steinhart_c=-7.4893966122e-08
            )
        """
        if steinhart_a is not None:
            self.STEINHART_A = steinhart_a
        if steinhart_b is not None:
            self.STEINHART_B = steinhart_b
        if steinhart_c is not None:
            self.STEINHART_C = steinhart_c
    
    def get_voltages(self) -> tuple[float, float]:
        """
        Get raw voltage readings for debugging.
        
        Returns:
            Tuple of (thermistor_voltage, reference_voltage)
        """
        return self._read_voltages()