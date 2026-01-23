# -*- coding: utf-8 -*-
# adc abstraction
# from __future__ import annotations

from busio import I2C  # type: ignore

from pioreactor import exc
from pioreactor import hardware
from pioreactor import types as pt
from pioreactor.version import hardware_version_info
from pioreactor.exc import HardwareNotFoundError
from pioreactor.logging import create_logger

from typing import Optional
import struct

import busio  # type: ignore
from adafruit_bus_device.i2c_device import I2CDevice
# from busio import I2C


logger = create_logger("adcs.py", experiment="NONE", unit="NONE", pub_client=None)


class ADC101C02x:
    # Registers
    _REG_CONV   = 0x00
    _REG_ALERT  = 0x01
    _REG_CONFIG = 0x02
    _REG_VLOW   = 0x03
    _REG_VHIGH  = 0x04
    _REG_VHYST  = 0x05
    _REG_VMIN   = 0x06
    _REG_VMAX   = 0x07

    # Cycle time field values (CONFIG[7:5])
    CYCLE_DISABLED = 0b000
    CYCLE_x32      = 0b001
    CYCLE_x64      = 0b010
    CYCLE_x128     = 0b011
    CYCLE_x256     = 0b100
    CYCLE_x512     = 0b101
    CYCLE_x1024    = 0b110
    CYCLE_x2048    = 0b111

    def __init__(self, i2c_address: int = 0x54) -> None:
        # SOT-6 021 default is 0x54; VSSOP-8/027 vary with ADR pins.
        comm = I2C(hardware.SCL, hardware.SDA)
        self._dev = I2CDevice(comm, i2c_address)
        self.address = i2c_address
        # Use fastest automatic conversion mode
        self.set_cycle(self.CYCLE_x32)

    # --- low-level ---
    def _write8(self, reg: int, val: int) -> None:
        buf = bytes([reg, val & 0xFF])
        with self._dev as i2c:
            i2c.write(buf)

    def _write16(self, reg: int, val: int) -> None:
        msb = (val >> 8) & 0xFF
        lsb = val & 0xFF
        with self._dev as i2c:
            i2c.write(bytes([reg, msb, lsb]))

    def _read8(self, reg: int) -> int:
        out = bytearray(1)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([reg]), out)
        return out[0]

    def _read16(self, reg: int) -> int:
        out = bytearray(2)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([reg]), out)
        return struct.unpack(">H", out)[0]  # device sends MSB first

    # --- public API ---
    def test_connection(self) -> bool:
        try:
            _ = self._read16(self._REG_CONV)
            return True
        except Exception:
            return False

    def read_raw(self) -> int:
        """Return 10-bit code (0..1023)."""
        v = self._read16(self._REG_CONV)
        return (v >> 2) & 0x03FF  # D11..D2

    def read_voltage(self, vref: float) -> float:
        """Code→volts using supply/reference vref."""
        code = self.read_raw()
        return (code * vref) / 1024.0

    # --- configuration (CONFIG 0x02) ---
    def set_cycle(self, cycle_field: int) -> None:
        c = 0
        try:
            c = self._read8(self._REG_CONFIG)
        except Exception:
            pass
        c &= ~0b1110_0000
        c |= (cycle_field & 0x07) << 5
        self._write8(self._REG_CONFIG, c)

    def set_alert_hold(self, hold: bool) -> None:
        c = self._read8(self._REG_CONFIG)
        c = (c | (1 << 4)) if hold else (c & ~(1 << 4))
        self._write8(self._REG_CONFIG, c)

    def set_alert_flag_enable(self, en: bool) -> None:
        c = self._read8(self._REG_CONFIG)
        c = (c | (1 << 3)) if en else (c & ~(1 << 3))
        self._write8(self._REG_CONFIG, c)

    def set_alert_pin_enable(self, en: bool) -> None:
        c = self._read8(self._REG_CONFIG)
        c = (c | (1 << 2)) if en else (c & ~(1 << 2))
        self._write8(self._REG_CONFIG, c)

    def set_alert_polarity_active_high(self, active_high: bool) -> None:
        c = self._read8(self._REG_CONFIG)
        c = (c | 0x01) if active_high else (c & ~0x01)
        self._write8(self._REG_CONFIG, c)

    # --- limits / hysteresis ---
    @staticmethod
    def _to_reg_16_from_code(code10: int) -> int:
        return (code10 & 0x03FF) << 2

    @staticmethod
    def _from_reg_16_to_code(val16: int) -> int:
        return (val16 >> 2) & 0x03FF

    def set_low_limit(self, code10: int) -> None:
        self._write16(self._REG_VLOW, self._to_reg_16_from_code(code10))

    def set_high_limit(self, code10: int) -> None:
        self._write16(self._REG_VHIGH, self._to_reg_16_from_code(code10))

    def set_hysteresis(self, code10: int) -> None:
        self._write16(self._REG_VHYST, self._to_reg_16_from_code(code10))

    # --- min / max (auto mode only) ---
    def read_min(self) -> int:
        return self._from_reg_16_to_code(self._read16(self._REG_VMIN))

    def read_max(self) -> int:
        return self._from_reg_16_to_code(self._read16(self._REG_VMAX))

    def clear_min(self) -> None:
        self._write16(self._REG_VMIN, 0x0FFF)

    def clear_max(self) -> None:
        self._write16(self._REG_VMAX, 0x0000)

    # --- alerts ---
    def read_alert_status(self) -> int:
        return self._read8(self._REG_ALERT) & 0x03  # bit1=over, bit0=under

    def clear_alerts(self, mask: int = 0x03) -> None:
        self._write8(self._REG_ALERT, mask & 0x03)


class _ADC:
    gain: float = 1

    def read_from_channel(self, channel: pt.AdcChannel) -> pt.AnalogValue:
        raise NotImplementedError

    def from_voltage_to_raw(self, voltage: pt.Voltage) -> pt.AnalogValue:
        raise NotImplementedError

    def from_raw_to_voltage(self, raw: pt.AnalogValue) -> pt.Voltage:
        raise NotImplementedError

    def check_on_gain(self, value: pt.Voltage, tol: float = 0.85) -> None:
        raise NotImplementedError


class ADS1115_ADC(_ADC):
    DATA_RATE = 128
    ADS1X15_GAIN_THRESHOLDS = {
        2 / 3: (4.096, 6.144),
        1: (2.048, 4.096),
        2: (1.024, 2.048),
        4: (0.512, 1.024),
        8: (0.256, 0.512),
        16: (-1, 0.256),
    }

    ADS1X15_PGA_RANGE = {
        2 / 3: 6.144,
        1: 4.096,
        2: 2.048,
        4: 1.024,
        8: 0.512,
        16: 0.256,
    }
    gain: float = 1.0

    def __init__(self) -> None:
        super().__init__()

        from adafruit_ads1x15.analog_in import AnalogIn  # type: ignore
        from adafruit_ads1x15.ads1115 import ADS1115 as ADS  # type: ignore

        self.analog_in: dict[int, AnalogIn] = {}

        self._ads = ADS(
            I2C(hardware.SCL, hardware.SDA),
            data_rate=self.DATA_RATE,
            gain=self.gain,
            address=hardware.ADC,
        )
        for channel in (0, 1, 2, 3):
            self.analog_in[channel] = AnalogIn(self._ads, channel)

    def check_on_gain(self, value: pt.Voltage, tol: float = 0.85) -> None:
        for gain, (lb, ub) in self.ADS1X15_GAIN_THRESHOLDS.items():
            if (tol * lb <= value < tol * ub) and (self.gain != gain):
                self.gain = gain
                self.set_ads_gain(gain)
                break

    def set_ads_gain(self, gain: float) -> None:
        self._ads.gain = gain  # this assignment will check to see if the gain is allowed.

    def from_voltage_to_raw(self, voltage: pt.Voltage) -> pt.AnalogValue:
        # from https://github.com/adafruit/Adafruit_CircuitPython_ADS1x15/blob/e33ed60b8cc6bbd565fdf8080f0057965f816c6b/adafruit_ads1x15/analog_in.py#L61
        return int(voltage * 32767 / self.ADS1X15_PGA_RANGE[self.gain])

    def from_voltage_to_raw_precise(self, voltage: pt.Voltage) -> pt.AnalogValue:
        return voltage * 32767 / self.ADS1X15_PGA_RANGE[self.gain]

    def from_raw_to_voltage(self, raw: pt.AnalogValue) -> pt.Voltage:
        # from https://github.com/adafruit/Adafruit_CircuitPython_ADS1x15/blob/e33ed60b8cc6bbd565fdf8080f0057965f816c6b/adafruit_ads1x15/analog_in.py#L61
        return raw / 32767 * self.ADS1X15_PGA_RANGE[self.gain]

    def read_from_channel(self, channel: pt.AdcChannel) -> pt.AnalogValue:
        assert 0 <= channel <= 3
        return self.analog_in[channel].value


class Pico_ADC(_ADC):
    def __init__(self) -> None:
        # set up i2c connection to hardware.ADC
        self.i2c = I2C(hardware.SCL, hardware.SDA)

    def read_from_channel(self, channel: pt.AdcChannel) -> pt.AnalogValue:
        assert 0 <= channel <= 3
        result = bytearray(2)
        try:
            self.i2c.writeto_then_readfrom(
                hardware.ADC, bytes([channel + 4]), result
            )  # + 4 is the i2c pointer offset
            return int.from_bytes(result, byteorder="little", signed=False)
        except OSError:
            raise exc.HardwareNotFoundError(
                f"Unable to find i2c channel {hardware.ADC}. Is the HAT attached? Is the firmware loaded?"
            )

    def from_voltage_to_raw(self, voltage: pt.Voltage) -> pt.AnalogValue:
        return int((voltage / 3.3) * 4095 * 16)

    def from_voltage_to_raw_precise(self, voltage: pt.Voltage) -> float:
        return (voltage / 3.3) * 4095 * 16

    def from_raw_to_voltage(self, raw: pt.AnalogValue) -> pt.Voltage:
        return (raw / 4095 / 16) * 3.3

    def check_on_gain(self, value: pt.Voltage, tol: float = 0.85) -> None:
        # pico has no gain.
        pass


ADC = ADS1115_ADC if (0, 0) < hardware_version_info <= (1, 0) else Pico_ADC
