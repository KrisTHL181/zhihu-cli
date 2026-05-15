"""Extension plugin system for zhihu-cli.

Each extension lives in its own subdirectory and exposes a ``register_cli(group)``
function that adds its commands to a click group.  Extensions are auto-discovered
and registered at startup.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def discover_extensions() -> list[ModuleType]:
    """Find all extension packages under the extensions/ directory.

    An extension is any subdirectory (not starting with ``_`` or ``.``) that
    contains an ``__init__.py`` with a ``register_cli`` callable.
    """
    extensions: list[ModuleType] = []
    extensions_dir = Path(__file__).parent

    for entry in sorted(extensions_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("_") or entry.name.startswith("."):
            continue
        init_file = entry / "__init__.py"
        if not init_file.exists():
            continue

        try:
            mod = importlib.import_module(f"zhihu_cli.extensions.{entry.name}")
            if hasattr(mod, "register_cli") and callable(mod.register_cli):
                extensions.append(mod)
        except ImportError as e:
            # Extension has unmet dependencies — skip it gracefully
            import sys

            print(f"Warning: failed to load extension '{entry.name}': {e}", file=sys.stderr)

    return extensions
