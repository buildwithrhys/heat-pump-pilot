"""Heat Pump Pilot: self-learning heat pump and home energy optimiser.

The package is organised so that the modelling, pricing and optimisation code
never imports Home Assistant specifics. Integrations live under
``heat_pump_pilot.integrations`` and translate external systems into the canonical
signals defined in ``heat_pump_pilot.domain``.
"""

__version__ = "0.9.1"
