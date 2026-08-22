# -*- coding: utf-8 -*-
"""Historical import surface for bootstrap pipeline dependency composition."""

from src.bootstrap.pipeline_dependencies import (
    PipelineDependencies,
    build_pipeline_dependencies,
)

__all__ = ["PipelineDependencies", "build_pipeline_dependencies"]
__architecture_forward_to__ = "src.bootstrap.pipeline_dependencies"
