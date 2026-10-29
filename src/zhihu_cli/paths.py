"""Location of the per-user zhihu-cli data directory.

Everything that used to hardcode ``~/.zhihu-cli`` derives from
:func:`zhihu_home` instead, so a test run or a sandbox can relocate the whole
tree with one environment variable.

The value is frozen at import time for most callers: the daemon state file, the
LLM config, and the ``CacheManager`` singleton are all module-level constants.
Set ``ZHIHU_CLI_HOME`` before the first ``import zhihu_cli``.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV = "ZHIHU_CLI_HOME"

#: Leaf directory used by :func:`zhihu_home` when the env var is unset.
_DEFAULT_DIRNAME = ".zhihu-cli"


def zhihu_home() -> Path:
    """Return the root of the per-user zhihu-cli data tree.

    ``ZHIHU_CLI_HOME`` overrides the default when set to a non-empty value;
    a leading ``~`` in the override is expanded.  The directory is not
    created — callers that need it create it themselves, so a read-only
    caller such as :func:`Path.unlink` does not leave an empty tree behind.

    :returns: The configured data directory, or ``~/.zhihu-cli``.
    """
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_DIRNAME
