# -*- coding: utf-8 -*-
"""Stable public facade for application settings."""

from __future__ import annotations

from importlib import import_module
import logging
import sys

_PUBLIC_MODULE_NAME = __name__
_IMPL_MODULE_NAME = "src.bootstrap.config_impl"
_public_module = sys.modules[_PUBLIC_MODULE_NAME]
_public_spec = getattr(_public_module, "__spec__", None)
_public_loader = getattr(_public_module, "__loader__", None)
_public_file = getattr(_public_module, "__file__", None)
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
_impl.__spec__ = _public_spec
_impl.__loader__ = _public_loader
if _public_file is not None:
    _impl.__file__ = _public_file
sys.modules[_PUBLIC_MODULE_NAME] = _impl
