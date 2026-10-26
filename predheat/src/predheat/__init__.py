"""PredHeat: self-learning heat pump and home energy optimiser.

The package is organised so that the modelling, pricing and optimisation code
never imports Home Assistant specifics. Integrations live under
``predheat.integrations`` and translate external systems into the canonical
signals defined in ``predheat.domain``.
"""

__version__ = "0.3.1"
