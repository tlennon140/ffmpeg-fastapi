"""
Application configuration using Pydantic Settings.

All configuration can be overridden via environment variables.
"""

from functools import lru_cache
from typing import List
import os

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # API Configuration
    API_KEYS: str = Field(
        default="dev-key-change-me",
        description="Comma-separated list of valid API keys"
    )
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = Field(
        default=100,
        description="Maximum requests per window"
    )
    RATE_LIMIT_WINDOW: int = Field(
        default=60,
        description="Rate limit window in seconds"
    )
    RATE_LIMIT_UPLOAD_REQUESTS: int = Field(
        default=10,
        description="Maximum upload requests per window"
    )
    
    # File Upload Limits
    MAX_UPLOAD_SIZE_MB: int = Field(
        default=500,
        description="Maximum upload file size in MB"
    )
    ALLOWED_VIDEO_EXTENSIONS: str = Field(
        default=".mp4,.avi,.mov,.mkv,.webm,.flv,.wmv",
        description="Comma-separated allowed video extensions"
    )
    ALLOWED_IMAGE_EXTENSIONS: str = Field(
        default=".jpg,.jpeg,.png,.gif,.bmp,.webp,.tiff",
        description="Comma-separated allowed image extensions"
    )
    
    # Directories
    TEMP_DIR: str = Field(
        default="/tmp/ffmpeg-api/temp",
        description="Temporary file directory"
    )
    OUTPUT_DIR: str = Field(
        default="/tmp/ffmpeg-api/output",
        description="Output file directory"
    )
    
    # FFMPEG Configuration
    FFMPEG_TIMEOUT: int = Field(
        default=300,
        description="FFMPEG operation timeout in seconds"
    )
    FFMPEG_THREADS: int = Field(
        default=0,
        description="FFMPEG threads (0 = auto)"
    )
    
    # CORS
    CORS_ORIGINS: str = Field(
        default="*",
        description="Comma-separated CORS origins"
    )
    
    # Documentation
    ENABLE_DOCS: bool = Field(
        default=True,
        description="Enable Swagger/ReDoc documentation"
    )
    
    # Cleanup
    FILE_RETENTION_SECONDS: int = Field(
        default=3600,
        description="How long to keep output files (seconds)"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
    
    @property
    def api_keys_list(self) -> List[str]:
        """Parse API keys into a list."""
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()]
    
    @property
    def allowed_video_extensions_list(self) -> List[str]:
        """Parse video extensions into a list."""
        return [e.strip().lower() for e in self.ALLOWED_VIDEO_EXTENSIONS.split(",")]
    
    @property
    def allowed_image_extensions_list(self) -> List[str]:
        """Parse image extensions into a list."""
        return [e.strip().lower() for e in self.ALLOWED_IMAGE_EXTENSIONS.split(",")]
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins into a list."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]
    
    @property
    def max_upload_size_bytes(self) -> int:
        """Get max upload size in bytes."""
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
