# -*- coding: utf-8 -*-
"""Architecture contracts for settings/bootstrap compatibility facades."""

from __future__ import annotations

import src.config as config
import src.bootstrap.config_impl as config_impl
import src.core.config_manager as config_manager
import src.bootstrap.config_manager_impl as config_manager_impl
import src.core.config_registry as config_registry
import src.bootstrap.config_registry_impl as config_registry_impl


def test_public_config_aliases_bootstrap_runtime() -> None:
    assert config is config_impl
    assert config.__architecture_bootstrap_impl__ == "src.bootstrap.config_impl"
    assert config.__name__ == "src.config"
    assert config.Config.__module__ == "src.config"


def test_config_registry_aliases_bootstrap_runtime() -> None:
    assert config_registry is config_registry_impl
    assert (
        config_registry.__architecture_bootstrap_impl__
        == "src.bootstrap.config_registry_impl"
    )
    assert config_registry.__name__ == "src.core.config_registry"


def test_config_manager_aliases_bootstrap_runtime() -> None:
    assert config_manager is config_manager_impl
    assert (
        config_manager.__architecture_bootstrap_impl__
        == "src.bootstrap.config_manager_impl"
    )
    assert config_manager.__name__ == "src.core.config_manager"
    assert config_manager.ConfigLineEntry.__module__ == "src.core.config_manager"
