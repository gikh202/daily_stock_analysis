# -*- coding: utf-8 -*-
"""Compatibility facade for bootstrap configuration file management."""

from __future__ import annotations

from importlib import import_module
import logging
import sys

_PUBLIC_MODULE_NAME = __name__
_IMPL_MODULE_NAME = "src.bootstrap.config_manager_impl"
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
_impl.__package__ = "src.core"
sys.modules[_PUBLIC_MODULE_NAME] = _impl
