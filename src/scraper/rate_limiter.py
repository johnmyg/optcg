"""Rate limiter to respect eBay's rate limits and avoid getting blocked."""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Token bucket rate limiter for controlling request frequency."""

    requests_per_second: float = 1.0
    burst_size: int = 5
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self):
        self._tokens = float(self.burst_size)
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.burst_size,
            self._tokens + elapsed * self.requests_per_second
        )
        self._last_refill = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Calculate wait time for next token
                wait_time = (1.0 - self._tokens) / self.requests_per_second
                await asyncio.sleep(wait_time)

    def acquire_sync(self) -> None:
        """Synchronous version of acquire for non-async contexts."""
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait_time = (1.0 - self._tokens) / self.requests_per_second
            time.sleep(wait_time)
