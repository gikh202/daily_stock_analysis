# -*- coding: utf-8 -*-
"""Stable public facade for application settings.

The configuration runtime lives under ``src.bootstrap`` because loading and
validating environment/application settings is a composition concern. Keep this
module import-compatible; do not add new configuration logic here.
"""

from __future__ import annotations

from importlib import import_module
import logging
import sys

_PUBLIC_MODULE_NAME = __name__
_IMPL_MODULE_NAME = "src.bootstrap.config_impl"
_impl = import_module(_IMPL_MODULE_NAME)
_impl.logger = logging.getLogger(_PUBLIC_MODULE_NAME)

for _value in list(vars(_impl).values()):
    if getattr(_value, "__module__", None) == _IMPL_MODULE_NAME:
        try:
            _value.__module__ = _PUBLIC_MODULE_NAME
        except (AttributeError, TypeError):
            pass

_impl.__architecture_bootstrap_impl__ = _IMPL_MODULE_NAME
_impl.__name__ = _PUBLIC_MODULE_NAME
_impl.__package__ = "src"
sys.modules[_PUBLIC_MODULE_NAME] = _impl
