"""Engine lifecycle: pick simulated vs pigpio, configure the pin map, build a driver."""

from __future__ import annotations

from stimpy_mcp.config import ServerConfig
from stimpy_mcp.core.models import PinMap
from stimpy_mcp.driver.stimulus import StimulusDriver
from stimpy_mcp.engine.base import StimulusEngine


def open_engine(config: ServerConfig) -> StimulusEngine:
    """Open the real pigpio engine, or the in-memory simulator if ``--simulate``."""
    if config.simulate:
        from stimpy_mcp.engine.simulated import SimulatedEngine

        return SimulatedEngine()
    from stimpy_mcp.engine.pigpio_engine import PigpioEngine

    return PigpioEngine(host=config.pigpio_host, port=config.pigpio_port)


def establish(engine: StimulusEngine, config: ServerConfig) -> StimulusDriver:
    """Configure ``engine`` with the configured pin map and return a driver."""
    pin_map = PinMap(data_pins=config.data_pins, sync_pin=config.sync_pin)
    engine.configure(pin_map)
    return StimulusDriver(engine, pin_map, default_clock_rate_hz=config.default_clock_rate_hz)
