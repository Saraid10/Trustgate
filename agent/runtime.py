"""Runtime compatibility helpers for the local buyer-agent command-line tools."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def run_async[Result](coroutine: Coroutine[Any, Any, Result]) -> Result:
    """Run async PostgreSQL work with Psycopg's supported Windows event loop."""

    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)
