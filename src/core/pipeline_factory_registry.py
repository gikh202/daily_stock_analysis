# -*- coding: utf-8 -*-
"""Compatibility alias for historical pipeline factory seams.

Canonical default factories live in ``src.bootstrap.pipeline_factory_registry``;
only the explicit legacy adapter preserves ``src.core.pipeline`` monkeypatches.
"""
from importlib import import_module as _import_module
import sys as _sys

_TARGET = "src.bootstrap.legacy_pipeline_factory_seams"
_impl = _import_module(_TARGET)
_impl.__architecture_forward_to__ = _TARGET
_sys.modules[__name__] = _impl
