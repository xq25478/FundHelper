from __future__ import annotations

import time

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (fund-helper)",
    "Accept": "*/*",
}


class RateLimiter:
    def __init__(self, per_sec: int) -> None:
        self.min_interval = 1.0 / max(per_sec, 1)
        self._last = 0.0

    def wait(self) -> None:
        gap = time.time() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.time()


def make_client(timeout: int = 10) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers=DEFAULT_HEADERS, follow_redirects=True)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4))
def fetch_text(client: httpx.Client, url: str, params: dict | None = None) -> str:
    r = client.get(url, params=params)
    r.raise_for_status()
    return r.text
