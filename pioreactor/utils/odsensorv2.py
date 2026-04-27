# -*- coding: utf-8 -*-
# ODSensorV2 I2C driver
# Address 0x69, 400 kHz, little-endian floats
# Register map: see ODSensorV2/dev_aid/interfaces_mds/INTERFACES.md

from __future__ import annotations

import struct
from time import sleep

from busio import I2C  # type: ignore
from adafruit_bus_device.i2c_device import I2CDevice

from pioreactor import hardware
from pioreactor.exc import HardwareNotFoundError
from pioreactor.logging import create_logger

logger = create_logger("odsensorv2", experiment="NONE", unit="NONE", pub_client=None)

ODSENSORV2_ADDR = 0x69

# Read registers (5-byte response: [status][4 LE float])
REG_MEASURED_REFLECTANCE            = 0x10
REG_NORMALIZED_REFLECTANCE          = 0x11
REG_CALIBRATED_DENSITY              = 0x12
REG_GROWTH_RATE                     = 0x13
REG_LASER_POWER_MW                  = 0x14
REG_FILTERED_REFLECTANCE            = 0x15
REG_FILTERED_CALIBRATED_DENSITY     = 0x16
REG_FILTERED_CALIBRATED_GROWTH_RATE = 0x17
REG_CALIBRATED_ABSOLUTE_GROWTH_RATE = 0x18
REG_STATUS                          = 0x20
REG_READING_NUMBER                  = 0x30

# Write registers
REG_CONTROL     = 0x40
REG_LASER_POWER = 0x50

# Control commands (written to REG_CONTROL)
CMD_START    = 0x01
CMD_STOP     = 0x02
CMD_BLANK    = 0x03
CMD_CAL      = 0x04
CMD_STOP_PI        = 0x05
CMD_ERASE_INITIAL  = 0x06

# Status byte bit masks
STATUS_JOB_RUNNING        = 1 << 0
STATUS_HAS_BLANK          = 1 << 1
STATUS_HAS_INITIAL        = 1 << 2
STATUS_PI_RUNNING         = 1 << 3
STATUS_PI_STABLE          = 1 << 4
STATUS_REFLECTANCE_STABLE = 1 << 5


class ODSensorV2:
    """I2C driver for the ODSensorV2 external OD sensor."""

    def __init__(self) -> None:
        comm = I2C(hardware.SCL, hardware.SDA)
        self._dev = I2CDevice(comm, ODSENSORV2_ADDR)

    def test_connection(self) -> bool:
        try:
            self.read_status()
            return True
        except Exception:
            return False

    def read_float_register(self, reg: int) -> float:
        """Write register address, then read 5 bytes: [status][4 LE float bytes].

        Uses repeated START (write_then_readinto) to keep the bus held between
        the register address write and the data read — matching the Arduino
        Wire.endTransmission(false) + Wire.requestFrom() pattern.
        """
        out = bytearray(5)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([reg]), out)
        status = out[0]
        if status != 0x00:
            raise IOError(f"ODSensorV2 register 0x{reg:02X} returned error status 0x{status:02X}")
        return struct.unpack("<f", out[1:5])[0]

    def read_status(self) -> int:
        """Read the status byte from register 0x20."""
        out = bytearray(2)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([REG_STATUS]), out)
        return out[1]

    def read_reading_number(self) -> int:
        """Read the reading counter from register 0x30."""
        out = bytearray(5)
        with self._dev as i2c:
            i2c.write_then_readinto(bytes([REG_READING_NUMBER]), out)
        return struct.unpack("<I", out[1:5])[0]

    def send_command(self, cmd: int) -> None:
        """Write a control command to register 0x40."""
        with self._dev as i2c:
            i2c.write(bytes([REG_CONTROL, cmd]))

    def set_laser_power(self, power_pct: float) -> None:
        """Set laser power target (0-100%) via register 0x50."""
        payload = bytes([REG_LASER_POWER]) + struct.pack("<f", power_pct)
        with self._dev as i2c:
            i2c.write(payload)

    def wait_for_status_bit(self, bit_mask: int, timeout_s: float = 10.0) -> bool:
        """Poll status register until the given bit(s) are set, or timeout."""
        elapsed = 0.0
        interval = 0.3
        while elapsed < timeout_s:
            try:
                status = self.read_status()
                if status & bit_mask:
                    return True
            except (OSError, IOError):
                pass  # sensor busy processing command, try again
            sleep(interval)
            elapsed += interval
        return False

    # --- Convenience readers ---

    def read_measured_reflectance(self) -> float:
        return self.read_float_register(REG_MEASURED_REFLECTANCE)

    def read_normalized_reflectance(self) -> float:
        return self.read_float_register(REG_NORMALIZED_REFLECTANCE)

    def read_calibrated_density(self) -> float:
        return self.read_float_register(REG_CALIBRATED_DENSITY)

    def read_growth_rate(self) -> float:
        return self.read_float_register(REG_GROWTH_RATE)

    def read_laser_power_mw(self) -> float:
        return self.read_float_register(REG_LASER_POWER_MW)

    def read_filtered_reflectance(self) -> float:
        return self.read_float_register(REG_FILTERED_REFLECTANCE)

    def read_filtered_calibrated_density(self) -> float:
        return self.read_float_register(REG_FILTERED_CALIBRATED_DENSITY)

    def read_filtered_calibrated_growth_rate(self) -> float:
        return self.read_float_register(REG_FILTERED_CALIBRATED_GROWTH_RATE)

    def read_calibrated_absolute_growth_rate(self) -> float:
        return self.read_float_register(REG_CALIBRATED_ABSOLUTE_GROWTH_RATE)
