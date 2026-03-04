"""Project plugin interface + discovery for doctor matrix."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import RunDirContract
from .runtime import CheckSpec, MatrixRuntime


class ProjectPlugin(Protocol):
    """Project plugin contract for doctor matrix."""

    name: str

    def enabled(self, runtime: MatrixRuntime) -> bool:
        ...

    def checks(self, runtime: MatrixRuntime) -> list[CheckSpec]:
        ...

    def run_dir_contracts(self, runtime: MatrixRuntime) -> list[RunDirContract]:
        ...


@dataclass
class PluginDiscoveryError:
    project: str
    source: str
    error: str


@dataclass
class DiscoveredPlugin:
    project: str
    source: Path
    plugin: ProjectPlugin


@dataclass
class PluginDiscoveryResult:
    plugins: list[DiscoveredPlugin]
    errors: list[PluginDiscoveryError]


def _load_module(path: Path):
    module_name = f"doctor_matrix_plugin_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # Ensure decorators (e.g. @dataclass) can resolve module metadata.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_plugin(module, path: Path):
    if hasattr(module, "get_plugin") and callable(module.get_plugin):
        return module.get_plugin()
    if hasattr(module, "PLUGIN"):
        return module.PLUGIN
    raise RuntimeError(f"doctor plugin missing get_plugin()/PLUGIN in {path}")


def discover_project_plugins(
    repo_root: Path,
    runtime: MatrixRuntime,
    *,
    project_filter: set[str] | None,
) -> PluginDiscoveryResult:
    """Discover plugins from services/*/doctor_plugin.py deterministically."""

    plugins: list[DiscoveredPlugin] = []
    errors: list[PluginDiscoveryError] = []

    services_root = repo_root / "services"
    paths = sorted(services_root.glob("*/doctor_plugin.py")) if services_root.exists() else []

    for path in paths:
        project = path.parent.name
        try:
            module = _load_module(path)
            plugin = _resolve_plugin(module, path)
            name = str(getattr(plugin, "name", project))
            if project_filter and name not in project_filter and project not in project_filter:
                continue
            if not hasattr(plugin, "checks"):
                raise RuntimeError("plugin missing checks()")
            if not hasattr(plugin, "enabled"):
                raise RuntimeError("plugin missing enabled()")
            if not hasattr(plugin, "run_dir_contracts"):
                raise RuntimeError("plugin missing run_dir_contracts()")
            if not bool(plugin.enabled(runtime)):
                continue
            plugins.append(DiscoveredPlugin(project=name, source=path, plugin=plugin))
        except Exception as exc:  # noqa: BLE001
            if project_filter and project not in project_filter and path.parent.name not in project_filter:
                continue
            errors.append(
                PluginDiscoveryError(
                    project=project,
                    source=str(path),
                    error=str(exc),
                )
            )

    plugins.sort(key=lambda item: item.project)
    errors.sort(key=lambda item: item.project)
    return PluginDiscoveryResult(plugins=plugins, errors=errors)
