from __future__ import annotations

import asyncio

import httpx
import pytest

from src.web.app import app


@pytest.mark.parametrize("host", ["hannibee.art", "bias.hannibee.art"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_forwarded_http_redirect_preserves_url(host, method):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"http://{host}"
        ) as client:
            return await client.request(
                method, "/feed?sort=latest&name=a%20b", headers={"X-Forwarded-Proto": "http"}
            )

    response = asyncio.run(request())

    assert response.status_code == 308
    assert response.headers["location"] == f"https://{host}/feed?sort=latest&name=a%20b"


@pytest.mark.parametrize("forwarded_proto", [None, "https"])
def test_local_http_and_forwarded_https_are_served_without_redirect(forwarded_proto):
    async def request():
        headers = {"X-Forwarded-Proto": forwarded_proto} if forwarded_proto else {}
        # Heroku's internal connection can be HTTP even for HTTPS visitors.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://hannibee.art"
        ) as client:
            return await client.get("/healthz", headers=headers)

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "location" not in response.headers
