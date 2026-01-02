"""
Video endpoints for concatenating segments from URLs.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AnyHttpUrl, BaseModel, Field, model_validator

from app.services.ffmpeg_service import ffmpeg_service
from app.utils.auth import verify_api_key
from app.utils.files import (
    cleanup_files,
    generate_output_path,
    generate_temp_path,
    get_output_filename,
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


class VideoConcatResponse(BaseModel):
    """Response model for video concatenation."""
    success: bool
    filename: str
    message: str


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

        return VideoConcatResponse(
            success=True,
            filename=get_output_filename(output_path),
            message="Videos concatenated successfully"
        )

    finally:
        cleanup_files(*downloaded_paths, *segment_paths)
