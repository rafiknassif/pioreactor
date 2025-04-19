# -*- coding: utf-8 -*-
# dacs.py
from __future__ import annotations

from typing import cast

import busio  # type: ignore

from pioreactor import hardware
from pioreactor.types import LedDriverChannel
from pioreactor.exc import HardwareNotFoundError
from pioreactor.types import FloatBetween0and100
from pioreactor.version import hardware_version_info

from adafruit_bus_device.i2c_device import I2CDevice
from busio import I2C  

class MCP47CxBxx:
    """
    Driver for the MCP47CMB02 digital to analog converter. (can be easily modified to support others of this family by adapting for different bit depth)
    See datasheet: https://ww1.microchip.com/downloads/aemDocuments/documents/OTH/ProductDocuments/DataSheets/MCP47CXBXX-Data-Sheet-DS20006089B.pdf
    """

    def __init__(self, i2cAddress, resolution, logger):
        self.i2cAddress = i2cAddress
        self.resolution = resolution
        self.logger = logger

        match resolution: 
            case 8: 
                self.maxValue = 255
            case 10: 
                self.maxValue = 1023
            case 12:
                self.maxValue = 4095
            case _: 
                self.maxValue = 4095  # Default to 12-bit

        comm_port = I2C(hardware.SCL, hardware.SDA)
        self.i2c = I2CDevice(comm_port, self.i2cAddress)

        self.setPowerMode(0, 0)  # Normal mode on both ch
        self.setReference(0, 0)  # Use VDD as reference on both ch
        self.setGain(0, 0)  # 1x gain on both ch
        self.setOutput(0, 0)  # Set DAC outputs to 0 
        self.setOutput(1, 0)

        self.channels: list[LedDriverChannel] = ["DRV_A", "DRV_B"]
        # { "DRV_A": 0, "DRV_B": 1 }
        self.channel_idx = { ch: idx for idx, ch in enumerate(self.channels) }
    
    def testConnection(self):
        return self.i2c.probe(self.i2cAddress)  # responds with true if device responds
    
    def writei2c(self, command, data):
        command.extend(data)
        self.i2c.write(command)

    def set_intensity_to(self, channel, intensity):
        # TODO: account for the nonlinear current drive vs dac value here
        desiredOutput = int(intensity/100*255)  # Temporarily just map intensity to 0-255 scale
        self.logger.debug(f"channel: {channel} mapped to {self.channel_idx[channel]}")
        self.setOutput(self.channel_idx[channel], desiredOutput)
        self.logger.debug(f"setOutput({self.channel_idx[channel]}, {desiredOutput})")
        
    def setOutput(self, channel, value: int):
        if channel > 1 or value > self.maxValue:
            return False
        command = bytearray(1)
        command[0] = (channel << 3) & 0x1F
        data = bytearray(2)
        data[0] = 0x0
        data[1] = value & 0xFF
        self.writei2c(command, data)

    def setReference(self, ch0_ref, ch1_ref):
        if ch0_ref > 3 or ch1_ref > 3:
            return False; 
        # ch_ref 11 = buffered Vref, 10 = unbuffered Vref, 01 = Internal Bandgap, 00 = VDD
        command = bytearray(1)
        command[0] = (0x08 << 3)  # Config register for channel
        data = bytearray(2)
        data[1] = ch0_ref | (ch1_ref << 2)
        data[0] = 0
        self.writei2c(command, data)
    
    def setGain(self, ch0_2x, ch1_2x):
        command = bytearray(1)
        command[0] = (0x0a << 3);  #  Config register
        # Set gain bits in the second byte (MSB):
        # Bit 8 : Channel 0 gain
        # Bit 9 : Channel 1 gain
        data = bytearray(2)
        if ch0_2x:
            data[0] |= 1; # Bit 8
        if ch1_2x:
            data[0] |= 1 << 1; # Bit 9
        self.writei2c(command, data)


    def setPowerMode(self, ch0_mode, ch1_mode):
        if ch0_mode > 3 or ch1_mode > 3:
             return False
        # ch_mode 00 = normal, 01 = 1k pull-down, 10 = 100k pull-down, 11 = open-circuit
        command = bytearray(1)
        command[0] = 0x09 << 3  # Config register for channel
        
        data = bytearray(2)
        data[1] = ch0_mode | (ch1_mode << 2) 
        data[0] = 0
        self.writei2c(command, data)


class _DAC:
    A = 0
    B = 1
    C = 2
    D = 3

    def set_intensity_to(self, channel: int, intensity: FloatBetween0and100) -> None:
        # float is a value between 0 and 100 inclusive
        pass


class DAC43608_DAC(_DAC):
    A = 8
    B = 9
    C = 10
    D = 11

    def __init__(self) -> None:
        from DAC43608 import DAC43608

        self.dac = DAC43608(address=hardware.DAC)

    def set_intensity_to(self, channel: int, intensity: FloatBetween0and100) -> None:
        from DAC43608 import Channel

        channel = cast(Channel, channel)
        if intensity == 0.0:
            self.dac.power_down(channel)
        else:
            self.dac.power_up(channel)
            self.dac.set_intensity_to(channel, intensity / 100.0)  # type: ignore


class Pico_DAC(_DAC):
    """
    The DAC is an 8-bit controller implemented in the Pico firmware. See pico-build repository for details.
    """

    A = 0
    B = 1
    C = 2
    D = 3

    def __init__(self) -> None:
        # set up i2c connection to hardware.DAC
        self.i2c = busio.I2C(hardware.SCL, hardware.SDA)

    def set_intensity_to(self, channel: int, intensity: FloatBetween0and100) -> None:
        try:
            # to 8 bit integer
            eight_bit = round((intensity / 100) * 255)
            self.i2c.writeto(hardware.DAC, bytes([channel, eight_bit]))
        except OSError:
            raise HardwareNotFoundError(
                f"Unable to find i2c channel {hardware.DAC}. Is the HAT attached? Is the firmware loaded?"
            )


DAC = DAC43608_DAC if (0, 0) < hardware_version_info <= (1, 0) else Pico_DAC
