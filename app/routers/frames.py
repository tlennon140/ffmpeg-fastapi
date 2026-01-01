"""
Frame extraction endpoints for videos.
"""

import logging
import os
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.ffmpeg_service import ffmpeg_service
from app.utils.auth import verify_api_key
from app.utils.files import (
    cleanup_file,
    cleanup_files,
    generate_output_path,
    get_output_filename,
    save_upload_file,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class FrameExtractionResponse(BaseModel):
    """Response model for frame extraction."""
    success: bool
    frame_count: int
    filename: str
    message: str


class LastFrameResponse(BaseModel):
    """Response model for last frame extraction."""
    success: bool
    filename: str
    video_duration: float
    message: str


@router.post(
    "/frames/extract",
    response_model=FrameExtractionResponse,
    summary="Extract frames from video",
    description="Extract frames at regular intervals from a video."
)
async def extract_frames(
    video: UploadFile = File(..., description="Video file"),
    fps: float = Form(
        default=1.0,
        gt=0,
        le=30,
        description="Frames per second to extract (e.g., 0.5 = every 2 seconds, 2 = twice per second)"
    ),
    format: str = Form(
        default="jpg",
        description="Output image format (jpg or png)"
    ),
    quality: int = Form(
        default=2,
        ge=1,
        le=31,
        description="JPEG quality (1=best, 31=worst). Only applies to JPG format."
    ),
    api_key: str = Depends(verify_api_key)
):
    """
    Extract frames from a video at regular intervals.
    
    **FPS Examples:**
    - `0.1` = 1 frame every 10 seconds
    - `0.5` = 1 frame every 2 seconds
    - `1.0` = 1 frame per second
    - `2.0` = 2 frames per second
    - `5.0` = 5 frames per second
    
    **Output:**
    Returns a ZIP file containing all extracted frames.
    
    **Supported video formats:** MP4, AVI, MOV, MKV, WebM, FLV, WMV
    """
    # Validate format
    if format.lower() not in ["jpg", "jpeg", "png"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format must be 'jpg' or 'png'"
        )
    
    format = "jpg" if format.lower() in ["jpg", "jpeg"] else "png"
    
    # Save uploaded video
    input_path, _ = await save_upload_file(
        video,
        settings.allowed_video_extensions_list,
        prefix="frames_input_"
    )
    
    # Create output directory for frames
    import uuid
    frames_dir = os.path.join(settings.TEMP_DIR, f"frames_{uuid.uuid4().hex[:8]}")
    os.makedirs(frames_dir, exist_ok=True)
    
    output_pattern = os.path.join(frames_dir, f"frame_%04d.{format}")
    
    extracted_frames = []
    zip_path = None
    
    try:
        # Extract frames
        result = await ffmpeg_service.extract_frames(
            video_path=input_path,
            output_pattern=output_pattern,
            fps=fps,
            format=format,
            quality=quality
        )
        
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to extract frames: {result.error}"
            )
        
        extracted_frames = result.output_paths or []
        
        if not extracted_frames:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No frames could be extracted from the video"
            )
        
        # Create ZIP file with frames
        zip_path = generate_output_path("frames_", ".zip")
        
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for frame_path in extracted_frames:
                arcname = os.path.basename(frame_path)
                zf.write(frame_path, arcname)
        
        return FrameExtractionResponse(
            success=True,
            frame_count=len(extracted_frames),
            filename=get_output_filename(zip_path),
            message=f"Extracted {len(extracted_frames)} frames at {fps} fps"
        )
        
    finally:
        # Cleanup
        cleanup_file(input_path)
        # Cleanup individual frames
        for frame in extracted_frames:
            cleanup_file(frame)
        # Cleanup frames directory
        if os.path.exists(frames_dir):
            import shutil
            shutil.rmtree(frames_dir, ignore_errors=True)


@router.post(
    "/frames/last",
    response_model=LastFrameResponse,
    summary="Extract last frame",
    description="Extract the last frame from a video."
)
async def extract_last_frame(
    video: UploadFile = File(..., description="Video file"),
    format: str = Form(
        default="jpg",
        description="Output image format (jpg or png)"
    ),
    quality: int = Form(
        default=2,
        ge=1,
        le=31,
        description="JPEG quality (1=best, 31=worst). Only applies to JPG format."
    ),
    api_key: str = Depends(verify_api_key)
):
    """
    Extract the last frame from a video.
    
    This is useful for generating video thumbnails or previews.
    
    **Supported video formats:** MP4, AVI, MOV, MKV, WebM, FLV, WMV
    """
    # Validate format
    if format.lower() not in ["jpg", "jpeg", "png"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format must be 'jpg' or 'png'"
        )
    
    ext = ".jpg" if format.lower() in ["jpg", "jpeg"] else ".png"
    
    # Save uploaded video
    input_path, _ = await save_upload_file(
        video,
        settings.allowed_video_extensions_list,
        prefix="lastframe_input_"
    )
    
    # Generate output path
    output_path = generate_output_path("last_frame_", ext)
    
    try:
        # Extract last frame
        result = await ffmpeg_service.extract_last_frame(
            video_path=input_path,
            output_path=output_path,
            quality=quality
        )
        
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to extract last frame: {result.error}"
            )
        
        return LastFrameResponse(
            success=True,
            filename=get_output_filename(output_path),
            video_duration=result.duration or 0,
            message="Last frame extracted successfully"
        )
        
    finally:
        # Cleanup input file
        cleanup_file(input_path)


@router.get(
    "/frames/download/{filename}",
    summary="Download extracted frames",
    description="Download extracted frames or ZIP file."
)
async def download_frames(
    filename: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Download extracted frames.
    
    For multiple frames, this returns a ZIP file.
    For single frame extraction, this returns the image directly.
    """
    filepath = os.path.join(settings.OUTPUT_DIR, filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or expired"
        )
    
    # Determine media type
    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        ".zip": "application/zip",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    
    media_type = media_types.get(ext, "application/octet-stream")
    
    return FileResponse(
        filepath,
        media_type=media_type,
        filename=filename
    )
