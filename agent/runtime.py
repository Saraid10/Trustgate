"""Runtime compatibility helpers for the local buyer-agent command-line tools."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any


def load_local_env() -> None:
    """Load the ignored local `.env` for host-run commands.

    Docker Compose reads `.env` through `env_file`, but a command run directly on the host does
    not, so configuration set there was silently invisible outside a container. This is called
    from command-line entry points only, never on library import, and never overrides a variable
    that is already set, so a container or CI environment still wins.
    """

    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:  # pragma: no cover - dotenv is a declared dependency
        return
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def run_async[Result](coroutine: Coroutine[Any, Any, Result]) -> Result:
    """Run async PostgreSQL work with Psycopg's supported Windows event loop."""

    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)
