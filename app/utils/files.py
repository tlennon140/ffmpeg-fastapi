"""
File handling utilities for uploads and downloads.
"""

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import HTTPException, UploadFile, status

from app.config import settings

logger = logging.getLogger(__name__)


def validate_file_extension(
    filename: str,
    allowed_extensions: List[str]
) -> str:
    """
    Validate file extension against allowed list.
    
    Args:
        filename: Original filename
        allowed_extensions: List of allowed extensions (with dots)
        
    Returns:
        The file extension (lowercase, with dot)
        
    Raises:
        HTTPException: If extension is not allowed
    """
    ext = Path(filename).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' not allowed. Allowed types: {', '.join(allowed_extensions)}"
        )
    return ext


async def save_upload_file(
    upload_file: UploadFile,
    allowed_extensions: List[str],
    prefix: str = ""
) -> Tuple[str, str]:
    """
    Save an uploaded file to the temp directory.
    
    Args:
        upload_file: The uploaded file
        allowed_extensions: List of allowed extensions
        prefix: Optional prefix for the saved filename
        
    Returns:
        Tuple of (saved_file_path, original_extension)
        
    Raises:
        HTTPException: If file is too large or has invalid extension
    """
    # Validate extension
    if not upload_file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required"
        )
    
    ext = validate_file_extension(upload_file.filename, allowed_extensions)
    
    # Generate unique filename
    unique_id = uuid.uuid4().hex[:12]
    filename = f"{prefix}{unique_id}{ext}"
    filepath = os.path.join(settings.TEMP_DIR, filename)
    
    # Ensure temp directory exists
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    
    # Save file with size check
    total_size = 0
    max_size = settings.max_upload_size_bytes
    
    try:
        with open(filepath, "wb") as buffer:
            while chunk := await upload_file.read(1024 * 1024):  # 1MB chunks
                total_size += len(chunk)
                if total_size > max_size:
                    # Clean up partial file
                    buffer.close()
                    os.remove(filepath)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File too large. Maximum size: {settings.MAX_UPLOAD_SIZE_MB}MB"
                    )
                buffer.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving upload: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save uploaded file"
        )
    
    logger.info(f"Saved upload: {filename} ({total_size} bytes)")
    return filepath, ext


def generate_output_path(prefix: str, extension: str) -> str:
    """
    Generate a unique output file path.
    
    Args:
        prefix: Filename prefix
        extension: File extension (with dot)
        
    Returns:
        Full path to output file
    """
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    unique_id = uuid.uuid4().hex[:12]
    filename = f"{prefix}{unique_id}{extension}"
    return os.path.join(settings.OUTPUT_DIR, filename)


def generate_temp_path(prefix: str, extension: str) -> str:
    """
    Generate a unique temp file path.
    
    Args:
        prefix: Filename prefix
        extension: File extension (with dot)
        
    Returns:
        Full path to temp file
    """
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    unique_id = uuid.uuid4().hex[:12]
    filename = f"{prefix}{unique_id}{extension}"
    return os.path.join(settings.TEMP_DIR, filename)


def cleanup_file(filepath: str) -> None:
    """
    Remove a file if it exists.
    
    Args:
        filepath: Path to file to remove
    """
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            logger.debug(f"Cleaned up: {filepath}")
    except Exception as e:
        logger.warning(f"Failed to cleanup {filepath}: {e}")


def cleanup_files(*filepaths: str) -> None:
    """
    Remove multiple files.
    
    Args:
        filepaths: Paths to files to remove
    """
    for filepath in filepaths:
        cleanup_file(filepath)


def get_output_filename(filepath: str) -> str:
    """
    Extract just the filename from a path.
    
    Args:
        filepath: Full file path
        
    Returns:
        Just the filename
    """
    return os.path.basename(filepath)
