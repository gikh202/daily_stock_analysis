# -*- coding: utf-8 -*-
"""Historical import surface for optional bootstrap dependency assembly."""

from src.bootstrap.pipeline_optional_dependencies import (
    OptionalPipelineDependencies,
    build_optional_pipeline_dependencies,
)

__all__ = [
    "OptionalPipelineDependencies",
    "build_optional_pipeline_dependencies",
]
__architecture_forward_to__ = "src.bootstrap.pipeline_optional_dependencies"
