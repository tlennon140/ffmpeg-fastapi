"""
Health check endpoints for monitoring and orchestration.
"""

import shutil
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str
    timestamp: str
    ffmpeg_available: bool
    version: str = "1.0.0"


class ReadinessResponse(BaseModel):
    """Readiness check response model."""
    ready: bool
    checks: dict


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Basic health check endpoint.
    
    Returns the service status and FFMPEG availability.
    """
    ffmpeg_path = shutil.which("ffmpeg")
    
    return HealthResponse(
        status="healthy" if ffmpeg_path else "degraded",
        timestamp=datetime.utcnow().isoformat(),
        ffmpeg_available=ffmpeg_path is not None,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check():
    """
    Readiness check for orchestration platforms.
    
    Verifies all dependencies are available.
    """
    # Check FFMPEG
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
    
    checks = {
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
    }
    
    all_ready = all(checks.values())
    
    return ReadinessResponse(
        ready=all_ready,
        checks=checks,
    )
