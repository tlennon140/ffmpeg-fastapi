"""
Rate limiting middleware using in-memory storage.

For production with multiple workers, consider using Redis.
"""

import time
from collections import defaultdict
from typing import Dict, Tuple
import asyncio
import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple in-memory rate limiter with sliding window."""
    
    def __init__(self):
        # Structure: {key: [(timestamp, count), ...]}
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def is_allowed(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> Tuple[bool, int, int]:
        """
        Check if a request is allowed under rate limits.
        
        Returns:
            Tuple of (allowed, remaining, reset_time)
        """
        async with self._lock:
            now = time.time()
            window_start = now - window_seconds
            
            # Clean old entries
            self._requests[key] = [
                (ts, count) for ts, count in self._requests[key]
                if ts > window_start
            ]
            
            # Count requests in window
            total_requests = sum(count for _, count in self._requests[key])
            
            if total_requests >= max_requests:
                # Calculate reset time
                if self._requests[key]:
                    oldest = min(ts for ts, _ in self._requests[key])
                    reset_time = int(oldest + window_seconds - now)
                else:
                    reset_time = window_seconds
                return False, 0, reset_time
            
            # Add this request
            self._requests[key].append((now, 1))
            remaining = max_requests - total_requests - 1
            
            return True, remaining, window_seconds
    
    async def cleanup(self):
        """Remove expired entries."""
        async with self._lock:
            now = time.time()
            max_window = max(
                settings.RATE_LIMIT_WINDOW,
                settings.RATE_LIMIT_WINDOW
            )
            cutoff = now - max_window
            
            for key in list(self._requests.keys()):
                self._requests[key] = [
                    (ts, count) for ts, count in self._requests[key]
                    if ts > cutoff
                ]
                if not self._requests[key]:
                    del self._requests[key]


# Global rate limiter instance
rate_limiter = RateLimiter()


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limits per API key."""
    
    # Paths that don't require rate limiting
    EXEMPT_PATHS = {"/", "/health", "/health/ready", "/docs", "/redoc", "/openapi.json"}
    
    # Paths with stricter upload limits
    UPLOAD_PATHS = {
        "/api/v1/captions/video",
        "/api/v1/captions/image",
        "/api/v1/frames/extract",
        "/api/v1/frames/last",
        "/api/v1/storage/r2/upload",
        "/api/v1/videos/audio",
        "/api/v1/videos/aspect",
        "/api/v1/videos/crop/vertical",
    }
    UPLOAD_PATH_PREFIXES = {"/api/v1/storage/r2/upload/output/"}
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request through rate limiter."""
        path = request.url.path
        
        # Skip rate limiting for exempt paths
        if path in self.EXEMPT_PATHS:
            return await call_next(request)
        
        # Get API key for rate limit key
        api_key = request.headers.get("X-API-Key", "anonymous")
        
        # Determine rate limit based on path
        is_upload = path in self.UPLOAD_PATHS or any(
            path.startswith(prefix) for prefix in self.UPLOAD_PATH_PREFIXES
        )
        if is_upload and request.method == "POST":
            max_requests = settings.RATE_LIMIT_UPLOAD_REQUESTS
            rate_key = f"upload:{api_key}"
        else:
            max_requests = settings.RATE_LIMIT_REQUESTS
            rate_key = f"general:{api_key}"
        
        # Check rate limit
        allowed, remaining, reset_time = await rate_limiter.is_allowed(
            rate_key,
            max_requests,
            settings.RATE_LIMIT_WINDOW
        )
        
        if not allowed:
            logger.warning(f"Rate limit exceeded for key: {api_key[:8]}...")
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "retry_after": reset_time,
                },
                headers={
                    "Retry-After": str(reset_time),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                }
            )
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        
        return response
