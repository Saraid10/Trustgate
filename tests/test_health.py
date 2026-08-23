import pytest
from httpx import ASGITransport, AsyncClient

from api.app import app


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
