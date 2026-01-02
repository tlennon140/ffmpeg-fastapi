"""
Video endpoints for concatenating segments from URLs.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from app.config import settings
from app.services.ffmpeg_service import ffmpeg_service
from app.services.r2_service import r2_service
from app.utils.auth import verify_api_key
from app.utils.files import (
    cleanup_files,
    generate_output_path,
    generate_temp_path,
    get_output_filename,
    save_upload_file,
)

router = APIRouter()


class VideoSegment(BaseModel):
    """Single video segment definition."""
    url: AnyHttpUrl = Field(..., description="Video URL")
    start: float = Field(..., ge=0, description="Start time in seconds")
    end: float = Field(..., gt=0, description="End time in seconds")

    @model_validator(mode="after")
    def validate_times(self) -> "VideoSegment":
        if self.end <= self.start:
            raise ValueError("End time must be greater than start time")
        return self


class VideoConcatRequest(BaseModel):
    """Request model for video concatenation."""
    segments: List[VideoSegment] = Field(
        ...,
        min_length=1,
        description="List of video segments to concatenate"
    )
    upload: bool = Field(default=False, description="Upload result to R2")
    upload_location: Optional[str] = Field(
        default=None,
        description="Optional key prefix within the bucket"
    )


class VideoConcatResponse(BaseModel):
    """Response model for video concatenation."""
    success: bool
    filename: str
    message: str
    r2_key: Optional[str] = None
    r2_url: Optional[str] = None


class VideoAudioResponse(BaseModel):
    """Response model for video audio operations."""
    success: bool
    filename: str
    message: str
    r2_key: Optional[str] = None
    r2_url: Optional[str] = None


class VideoTransformResponse(BaseModel):
    """Response model for video transform operations."""
    success: bool
    filename: str
    message: str
    r2_key: Optional[str] = None
    r2_url: Optional[str] = None


@router.post(
    "/videos/concat",
    response_model=VideoConcatResponse,
    summary="Concatenate video segments",
    description="Download video URLs, trim segments, and concatenate into one video."
)
async def concat_videos(
    request: VideoConcatRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Concatenate multiple video segments from URLs.
    """
    downloaded_paths: List[str] = []
    segment_paths: List[str] = []
    target_width = None
    target_height = None
    output_path = generate_output_path("concat_", ".mp4")

    try:
        for index, segment in enumerate(request.segments):
            source_path = await ffmpeg_service.download_video_from_url(
                str(segment.url),
                prefix=f"concat_src_{index}_"
            )
            downloaded_paths.append(source_path)

            if target_width is None or target_height is None:
                target_width, target_height = await ffmpeg_service.get_media_dimensions(source_path)

            segment_output = generate_temp_path("concat_seg_", ".mp4")
            segment_paths.append(segment_output)

            result = await ffmpeg_service.trim_video_segment(
                input_path=source_path,
                output_path=segment_output,
                start=segment.start,
                end=segment.end,
                target_width=target_width,
                target_height=target_height
            )

            if not result.success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to trim segment: {result.error}"
                )

        concat_result = await ffmpeg_service.concat_segments(segment_paths, output_path)

        if not concat_result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to concatenate videos: {concat_result.error}"
            )

        r2_key = None
        r2_url = None
        if request.upload:
            upload_result = await r2_service.upload_file_path(
                file_path=output_path,
                filename=get_output_filename(output_path),
                key_prefix=request.upload_location or ""
            )
            r2_key = upload_result.key
            r2_url = upload_result.url

        return VideoConcatResponse(
            success=True,
            filename=get_output_filename(output_path),
            message="Videos concatenated successfully",
            r2_key=r2_key,
            r2_url=r2_url
        )

    finally:
        cleanup_files(*downloaded_paths, *segment_paths)


@router.post(
    "/videos/audio",
    response_model=VideoAudioResponse,
    summary="Add or replace audio on a video",
    description="Upload a video and audio file, then mix or replace audio."
)
async def add_audio_to_video(
    video: UploadFile = File(..., description="Video file"),
    audio: UploadFile = File(..., description="Audio file"),
    replace_audio: bool = Form(default=False, description="Replace original audio"),
    upload: bool = Form(default=False, description="Upload result to R2"),
    upload_location: Optional[str] = Form(
        default=None,
        description="Optional key prefix within the bucket"
    ),
    api_key: str = Depends(verify_api_key)
):
    """
    Add or replace audio on a video.
    """
    video_path = None
    audio_path = None
    output_path = None
    
    try:
        video_path, video_ext = await save_upload_file(
            video,
            settings.allowed_video_extensions_list,
            prefix="audio_video_"
        )
        audio_path, _ = await save_upload_file(
            audio,
            settings.allowed_audio_extensions_list,
            prefix="audio_track_"
        )
        
        output_path = generate_output_path("audio_", video_ext)
        
        result = await ffmpeg_service.add_audio_to_video(
            video_path=video_path,
            audio_path=audio_path,
            output_path=output_path,
            replace_audio=replace_audio
        )
        
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process audio: {result.error}"
            )
        
        r2_key = None
        r2_url = None
        if upload:
            upload_result = await r2_service.upload_file_path(
                file_path=output_path,
                filename=get_output_filename(output_path),
                key_prefix=upload_location or ""
            )
            r2_key = upload_result.key
            r2_url = upload_result.url
        
        return VideoAudioResponse(
            success=True,
            filename=get_output_filename(output_path),
            message="Audio processed successfully",
            r2_key=r2_key,
            r2_url=r2_url
        )
    
    finally:
        cleanup_files(*(path for path in [video_path, audio_path] if path))


@router.post(
    "/videos/aspect",
    response_model=VideoTransformResponse,
    summary="Convert video aspect ratio",
    description="Convert a video to 9:16, 1:1, or 16:9 using padding."
)
async def convert_aspect_ratio(
    video: UploadFile = File(..., description="Video file"),
    ratio: str = Form(default="9:16", description="Target ratio (9:16, 1:1, 16:9)"),
    background_color: str = Form(default="black", description="Padding color"),
    upload: bool = Form(default=False, description="Upload result to R2"),
    upload_location: Optional[str] = Form(
        default=None,
        description="Optional key prefix within the bucket"
    ),
    api_key: str = Depends(verify_api_key)
):
    """
    Convert a video's aspect ratio using padding.
    """
    video_path = None
    output_path = None
    
    try:
        video_path, video_ext = await save_upload_file(
            video,
            settings.allowed_video_extensions_list,
            prefix="aspect_video_"
        )
        output_path = generate_output_path("aspect_", video_ext)
        
        result = await ffmpeg_service.convert_aspect_ratio(
            video_path=video_path,
            output_path=output_path,
            target_ratio=ratio,
            background_color=background_color
        )
        
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to convert aspect ratio: {result.error}"
            )
        
        r2_key = None
        r2_url = None
        if upload:
            upload_result = await r2_service.upload_file_path(
                file_path=output_path,
                filename=get_output_filename(output_path),
                key_prefix=upload_location or ""
            )
            r2_key = upload_result.key
            r2_url = upload_result.url
        
        return VideoTransformResponse(
            success=True,
            filename=get_output_filename(output_path),
            message="Aspect ratio converted successfully",
            r2_key=r2_key,
            r2_url=r2_url
        )
    
    finally:
        cleanup_files(*(path for path in [video_path] if path))


@router.post(
    "/videos/crop/vertical",
    response_model=VideoTransformResponse,
    summary="Smart crop for vertical video",
    description="Crop a video to 9:16 (or other supported ratios)."
)
async def crop_vertical_video(
    video: UploadFile = File(..., description="Video file"),
    ratio: str = Form(default="9:16", description="Target ratio (9:16, 1:1, 16:9)"),
    upload: bool = Form(default=False, description="Upload result to R2"),
    upload_location: Optional[str] = Form(
        default=None,
        description="Optional key prefix within the bucket"
    ),
    api_key: str = Depends(verify_api_key)
):
    """
    Crop a video to a target ratio with a centered crop.
    """
    video_path = None
    output_path = None
    
    try:
        video_path, video_ext = await save_upload_file(
            video,
            settings.allowed_video_extensions_list,
            prefix="crop_video_"
        )
        output_path = generate_output_path("crop_", video_ext)
        
        result = await ffmpeg_service.smart_crop_video(
            video_path=video_path,
            output_path=output_path,
            target_ratio=ratio
        )
        
        if not result.success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to crop video: {result.error}"
            )
        
        r2_key = None
        r2_url = None
        if upload:
            upload_result = await r2_service.upload_file_path(
                file_path=output_path,
                filename=get_output_filename(output_path),
                key_prefix=upload_location or ""
            )
            r2_key = upload_result.key
            r2_url = upload_result.url
        
        return VideoTransformResponse(
            success=True,
            filename=get_output_filename(output_path),
            message="Video cropped successfully",
            r2_key=r2_key,
            r2_url=r2_url
        )
    
    finally:
        cleanup_files(*(path for path in [video_path] if path))
