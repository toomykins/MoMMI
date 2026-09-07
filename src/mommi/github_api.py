from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

import aiohttp

LOGGER = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
USER_AGENT = "MoMMI/3.0 (+https://github.com/toomykins/MoMMI)"
DEFAULT_ACCEPT = "application/vnd.github+json"
TIMEOUT = 20.0


CACHE_SIZE = 512


class GitHubError(Exception):

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub returned {status}: {message}")
        self.status = status
        self.body = message


class RateLimited(GitHubError):
    pass


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        self._token = token
        self._session: aiohttp.ClientSession | None = None

        self._cache: OrderedDict[tuple[str, str, str], tuple[str, Any]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                headers = {"User-Agent": USER_AGENT, "Accept": DEFAULT_ACCEPT}
                if self._token:
                    headers["Authorization"] = f"Bearer {self._token}"
                self._session = aiohttp.ClientSession(
                    headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT)
                )
            return self._session

    def url(self, path: str) -> str:
        return API_ROOT + path if path.startswith("/") else path

    async def get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        accept: str | None = None,
    ) -> Any:
        url = self.url(path)
        accept = accept or DEFAULT_ACCEPT
        key = (url, repr(sorted((params or {}).items())), accept)

        headers = {"Accept": accept}
        cached = self._cache.get(key)
        if cached is not None:
            headers["If-None-Match"] = cached[0]

        session = await self.session()
        async with session.get(url, params=params, headers=headers) as response:
            if response.status == 304 and cached is not None:
                self._cache.move_to_end(key)
                return cached[1]

            if response.status == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
                reset = response.headers.get("X-RateLimit-Reset", "?")
                raise RateLimited(response.status, f"rate limit exhausted, resets at {reset}")

            if response.status != 200:
                raise GitHubError(response.status, (await response.text())[:300])

            body = await response.json()
            etag = response.headers.get("ETag")

        if etag:
            self._cache[key] = (etag, body)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return body

    async def get_paginated_count(self, path: str, *, params: dict[str, str] | None = None) -> int:
        session = await self.session()
        async with session.get(self.url(path), params=params) as response:
            if response.status != 200:
                raise GitHubError(response.status, (await response.text())[:300])
            link = response.headers.get("Link", "")

        import re

        match = re.search(r'[?&]page=(\d+)[^,]*>;\s*rel="last"', link)
        return int(match.group(1)) if match else 1

    async def post(self, path: str, payload: Any, *, accept: str | None = None) -> tuple[int, Any]:
        session = await self.session()
        headers = {"Accept": accept} if accept else {}
        async with session.post(self.url(path), json=payload, headers=headers) as response:
            body: Any
            try:
                body = await response.json()
            except (aiohttp.ContentTypeError, ValueError):
                body = await response.text()
            return response.status, body

    async def delete(self, path: str) -> int:
        session = await self.session()
        async with session.delete(self.url(path)) as response:
            return response.status

    async def create_gist(self, contents: str, filename: str, description: str) -> str:
        status, body = await self.post(
            "/gists",
            {
                "description": description,
                "public": False,
                "files": {filename: {"content": contents}},
            },
        )
        if status != 201:
            return f"[GIST ERROR: {status}]"
        return str(body.get("html_url", "[GIST ERROR: no url]"))
