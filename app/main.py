"""
FastAPI FFMPEG Media Processing API

A production-ready API for video and image processing using FFMPEG.
"""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.middleware.rate_limiter import RateLimiterMiddleware
from app.routers import captions, frames, health, storage, videos
from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown events."""
    # Startup
    logger.info("Starting FFMPEG Media Processing API")
    
    # Ensure temp directories exist
    Path(settings.TEMP_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    # Verify FFMPEG is available
    import shutil
    if not shutil.which("ffmpeg"):
        logger.error("FFMPEG not found in PATH!")
        raise RuntimeError("FFMPEG is required but not found")
    
    logger.info("FFMPEG found and ready")
    yield
    
    # Shutdown
    logger.info("Shutting down FFMPEG Media Processing API")
    
    # Cleanup temp files
    import shutil
    if os.path.exists(settings.TEMP_DIR):
        shutil.rmtree(settings.TEMP_DIR, ignore_errors=True)


app = FastAPI(
    title="FFMPEG Media Processing API",
    description="""
## Video and Image Processing API

This API provides endpoints for:
- **Captions**: Add captions/subtitles to videos and text overlays to images
- **Frames**: Extract frames from videos at regular intervals or specific positions
- **Videos**: Concatenate video segments from URLs
- **Storage**: Upload files or outputs to Cloudflare R2

### Authentication
All endpoints (except health checks) require an API key passed via the `X-API-Key` header.

### Rate Limiting
- Default: 100 requests per minute per API key
- File uploads: 10 requests per minute per API key
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiter middleware
app.add_middleware(RateLimiterMiddleware)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(captions.router, prefix="/api/v1", tags=["Captions"])
app.include_router(frames.router, prefix="/api/v1", tags=["Frames"])
app.include_router(videos.router, prefix="/api/v1", tags=["Videos"])
app.include_router(storage.router, prefix="/api/v1", tags=["Storage"])


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint redirect to docs."""
    return {
        "message": "FFMPEG Media Processing API",
        "docs": "/docs",
        "health": "/health",
    }
