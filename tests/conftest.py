import asyncio

from fixtures import async_session as async_session
from fixtures import seeded_fixture_data as seeded_fixture_data


def pytest_asyncio_loop_factories() -> dict[str, type[asyncio.AbstractEventLoop]]:
    return {"selector": asyncio.SelectorEventLoop}
