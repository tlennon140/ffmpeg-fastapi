"""
Caption endpoints for adding text overlays to videos and images.
"""

import logging
import os
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.ffmpeg_service import ffmpeg_service
from app.utils.auth import verify_api_key
from app.utils.files import (
    cleanup_file,
    generate_output_path,
    get_output_filename,
    save_upload_file,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class Caption(BaseModel):
    """Single caption entry."""
    text: str = Field(..., description="Caption text")
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., gt=0, description="End time in seconds")


class VideoCaptionRequest(BaseModel):
    """Request model for video captioning."""
    captions: List[Caption] = Field(..., min_length=1, description="List of captions")
    font_size: int = Field(default=24, ge=8, le=72, description="Font size in pixels")
    font_color: str = Field(default="white", description="Font color")
    bg_color: str = Field(default="black@0.5", description="Background color with opacity")
    position: Literal["top", "center", "bottom"] = Field(
        default="bottom",
        description="Caption position"
    )


class ImageCaptionRequest(BaseModel):
    """Request model for image captioning."""
    text: str = Field(..., min_length=1, max_length=500, description="Caption text")
    font_size: int = Field(default=24, ge=8, le=72, description="Font size in pixels")
    font_color: str = Field(default="white", description="Font color")
    bg_color: str = Field(default="black@0.5", description="Background color with opacity")
    position: Literal["top", "center", "bottom", "custom"] = Field(
        default="bottom",
        description="Text position"
    )
    x_offset: int = Field(default=0, description="X offset for custom positioning")
    y_offset: int = Field(default=0, description="Y offset from position")


class CaptionResponse(BaseModel):
    """Response model for caption operations."""
    success: bool
    filename: str
    message: str


@router.post(
    "/captions/video",
    response_model=CaptionResponse,
    summary="Add captions to video",
    description="Upload a video and add timed captions/subtitles."
)
async def add_video_captions(
    video: UploadFile = File(..., description="Video file to caption"),
    captions_json: str = Form(
        ...,
        description='JSON array of captions: [{"text": "Hello", "start": 0, "end": 2}]'
    ),
    font_size: int = Form(default=24, ge=8, le=72),
    font_color: str = Form(default="white"),
    bg_color: str = Form(default="black@0.5"),
    position: str = Form(default="bottom"),
    api_key: str = Depends(verify_api_key)
):
    """
    Add captions/subtitles to a video.
    
    **Captions JSON Format:**
    ```json
    [
        {"text": "First caption", "start": 0.0, "end": 2.5},
        {"text": "Second caption", "start": 3.0, "end": 5.0}
    ]
    ```
    
    **Supported formats:** MP4, AVI, MOV, MKV, WebM, FLV, WMV
    """
    import json
    
    # Parse captions JSON
    try:
        captions_data = json.loads(captions_json)
        captions = [Caption(**c) for c in captions_data]
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid captions JSON: {str(e)}"
        )
    
    if not captions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one caption is required"
        )
    
    # Validate position
    if position not in ["top", "center", "bottom"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Position must be 'top', 'center', or 'bottom'"
        )
    
    # Save uploaded video
    input_path, ext = await save_upload_file(
        video,
        settings.allowed_video_extensions_list,
        prefix="caption_input_"
    )
    
    # Generate output path
    output_path = generate_output_path("captioned_", ext)
    
    try:
        # Process video
        result = await ffmpeg_service.add_captions_to_video(
            video_path=input_path,
            output_path=output_path,
            captions=[c.model_dump() for c in captions],
            font_size=font_size,
            font_color=font_color,
            bg_color=bg_color,
            position=position
        )
        
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to add captions: {result.error}"
            )
        
        return CaptionResponse(
            success=True,
            filename=get_output_filename(output_path),
            message="Video captioned successfully"
        )
        
    finally:
        # Cleanup input file
        cleanup_file(input_path)


@router.post(
    "/captions/image",
    response_model=CaptionResponse,
    summary="Add caption to image",
    description="Upload an image and add a text overlay."
)
async def add_image_caption(
    image: UploadFile = File(..., description="Image file to caption"),
    text: str = Form(..., min_length=1, max_length=500, description="Caption text"),
    font_size: int = Form(default=24, ge=8, le=72),
    font_color: str = Form(default="white"),
    bg_color: str = Form(default="black@0.5"),
    position: str = Form(default="bottom"),
    x_offset: int = Form(default=0),
    y_offset: int = Form(default=0),
    api_key: str = Depends(verify_api_key)
):
    """
    Add a text caption to an image.
    
    **Positions:**
    - `top`: Centered at top
    - `center`: Centered in middle
    - `bottom`: Centered at bottom
    - `custom`: Use x_offset and y_offset for precise placement
    
    **Supported formats:** JPG, PNG, GIF, BMP, WebP, TIFF
    """
    # Validate position
    if position not in ["top", "center", "bottom", "custom"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Position must be 'top', 'center', 'bottom', or 'custom'"
        )
    
    # Save uploaded image
    input_path, ext = await save_upload_file(
        image,
        settings.allowed_image_extensions_list,
        prefix="caption_input_"
    )
    
    # Generate output path (keep same format)
    output_path = generate_output_path("captioned_", ext)
    
    try:
        # Process image
        result = await ffmpeg_service.add_text_to_image(
            image_path=input_path,
            output_path=output_path,
            text=text,
            font_size=font_size,
            font_color=font_color,
            bg_color=bg_color,
            position=position,
            x_offset=x_offset,
            y_offset=y_offset
        )
        
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to add caption: {result.error}"
            )
        
        return CaptionResponse(
            success=True,
            filename=get_output_filename(output_path),
            message="Image captioned successfully"
        )
        
    finally:
        # Cleanup input file
        cleanup_file(input_path)


@router.get(
    "/captions/download/{filename}",
    summary="Download captioned file",
    description="Download a processed file by filename."
)
async def download_captioned_file(
    filename: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Download a captioned video or image.
    
    Use the filename returned from the caption endpoints.
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
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    
    media_type = media_types.get(ext, "application/octet-stream")
    
    return FileResponse(
        filepath,
        media_type=media_type,
        filename=filename
    )
