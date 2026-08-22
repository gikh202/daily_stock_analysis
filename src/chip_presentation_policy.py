# -*- coding: utf-8 -*-
"""Compatibility forwarder to the chip presentation policy."""
from importlib import import_module as _import_module
_TARGET = "src.presentation.policies.chip"
_impl = _import_module(_TARGET)
for _name, _value in vars(_impl).items():
    if _name not in {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__", "__builtins__"}:
        globals()[_name] = _value
__architecture_forward_to__ = _TARGET
