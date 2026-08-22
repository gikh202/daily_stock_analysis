# -*- coding: utf-8 -*-
"""Compatibility alias for the bootstrap pipeline composition root."""
from importlib import import_module as _import_module
import sys as _sys

_TARGET = "src.bootstrap.pipeline_dependencies"
_impl = _import_module(_TARGET)
_impl.__architecture_forward_to__ = _TARGET
_sys.modules[__name__] = _impl
