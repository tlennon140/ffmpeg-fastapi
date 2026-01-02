"""
FFMPEG service for video and image processing operations.
"""

import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status

from app.config import settings
from app.utils.files import cleanup_file, generate_temp_path

logger = logging.getLogger(__name__)

DEFAULT_CAPTION_FONT = "Arial"
DEFAULT_CAPTION_FONT_RATIO = 0.0175
DEFAULT_CAPTION_MIN_FONT_SIZE = 8
DEFAULT_CAPTION_MAX_FONT_SIZE = 72
DEFAULT_CAPTION_BORDER_RATIO = 0.12
DEFAULT_CAPTION_MIN_BORDER = 2
DEFAULT_CAPTION_MAX_BORDER = 10
ASS_COLOR_FALLBACK = "&H00FFFFFF"
ASS_OUTLINE_FALLBACK = "&H00000000"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


@dataclass
class FFMPEGResult:
    """Result of an FFMPEG operation."""
    success: bool
    output_path: Optional[str] = None
    output_paths: Optional[List[str]] = None
    error: Optional[str] = None
    duration: Optional[float] = None


class FFMPEGService:
    """Service for executing FFMPEG commands."""
    
    @staticmethod
    async def run_command(cmd: List[str], timeout: Optional[int] = None) -> Tuple[bool, str, str]:
        """
        Run an FFMPEG command asynchronously.
        
        Args:
            cmd: Command and arguments as list
            timeout: Optional timeout in seconds
            
        Returns:
            Tuple of (success, stdout, stderr)
        """
        timeout = timeout or settings.FFMPEG_TIMEOUT
        cmd_str = " ".join(shlex.quote(str(c)) for c in cmd)
        logger.info(f"Running FFMPEG: {cmd_str[:200]}...")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
            
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")
            
            success = process.returncode == 0
            
            if not success:
                logger.error(f"FFMPEG failed: {stderr_str[:500]}")
            
            return success, stdout_str, stderr_str
            
        except asyncio.TimeoutError:
            logger.error(f"FFMPEG timeout after {timeout}s")
            if process:
                process.kill()
            return False, "", f"Operation timed out after {timeout} seconds"
        except Exception as e:
            logger.error(f"FFMPEG error: {e}")
            return False, "", str(e)
    
    @staticmethod
    async def get_video_info(video_path: str) -> dict:
        """
        Get video information using ffprobe.
        
        Args:
            video_path: Path to video file
            
        Returns:
            Dictionary with video information
        """
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path
        ]
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to read video info: {stderr}"
            )
        
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid video file"
            )

    @staticmethod
    async def download_video_from_url(url: str, prefix: str = "remote_") -> str:
        """
        Download a remote video to a temp file.
        
        Args:
            url: HTTP/HTTPS URL of the video
            prefix: Filename prefix for the temp file
            
        Returns:
            Path to downloaded file
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Video URL must start with http:// or https://"
            )
        
        ext = Path(parsed.path).suffix.lower()
        if ext:
            if ext not in settings.allowed_video_extensions_list:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Video URL extension '{ext}' not allowed"
                )
        else:
            ext = ".mp4"
        
        output_path = generate_temp_path(prefix, ext)
        max_size = settings.max_upload_size_bytes
        timeout = httpx.Timeout(30.0, connect=10.0)
        
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Failed to download video (status {response.status_code})"
                        )
                    
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > max_size:
                                raise HTTPException(
                                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail=f"Video too large. Maximum size: {settings.MAX_UPLOAD_SIZE_MB}MB"
                                )
                        except ValueError:
                            pass
                    
                    total_size = 0
                    with open(output_path, "wb") as buffer:
                        async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                            if not chunk:
                                continue
                            total_size += len(chunk)
                            if total_size > max_size:
                                raise HTTPException(
                                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail=f"Video too large. Maximum size: {settings.MAX_UPLOAD_SIZE_MB}MB"
                                )
                            buffer.write(chunk)
        
        except HTTPException:
            cleanup_file(output_path)
            raise
        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            cleanup_file(output_path)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to download video"
            )
        
        return output_path

    @staticmethod
    async def get_media_dimensions(media_path: str) -> Tuple[int, int]:
        """
        Get media dimensions using ffprobe.
        
        Args:
            media_path: Path to media file
            
        Returns:
            Tuple of (width, height)
        """
        info = await FFMPEGService.get_video_info(media_path)
        
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                width = stream.get("width")
                height = stream.get("height")
                if width and height:
                    return int(width), int(height)
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not determine media dimensions"
        )

    @staticmethod
    async def _has_audio_stream(video_path: str) -> bool:
        """Check if the media has an audio stream."""
        info = await FFMPEGService.get_video_info(video_path)
        return any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))

    @staticmethod
    async def _resolve_font_size(media_path: str, font_size: Optional[int]) -> int:
        """Resolve font size, defaulting to a size relative to media height."""
        if font_size is not None:
            return font_size
        
        try:
            _, height = await FFMPEGService.get_media_dimensions(media_path)
        except HTTPException:
            return 24
        
        scaled_size = int(round(height * DEFAULT_CAPTION_FONT_RATIO))
        return max(
            DEFAULT_CAPTION_MIN_FONT_SIZE,
            min(scaled_size, DEFAULT_CAPTION_MAX_FONT_SIZE)
        )

    @staticmethod
    def _resolve_border_width(font_size: int) -> int:
        """Resolve border width relative to font size."""
        scaled_width = int(round(font_size * DEFAULT_CAPTION_BORDER_RATIO))
        return max(
            DEFAULT_CAPTION_MIN_BORDER,
            min(scaled_width, DEFAULT_CAPTION_MAX_BORDER)
        )

    @staticmethod
    def _ass_color(color: str, fallback: str) -> str:
        """Convert a hex or named color to ASS format (&H00BBGGRR)."""
        if not color:
            return fallback
        
        color = color.strip().lower()
        named = {
            "white": "ffffff",
            "black": "000000",
            "red": "ff0000",
            "green": "00ff00",
            "blue": "0000ff",
            "yellow": "ffff00",
        }
        
        if color in named:
            hex_color = named[color]
        elif color.startswith("#") and len(color) == 7:
            hex_color = color[1:]
        elif len(color) == 6 and all(c in "0123456789abcdef" for c in color):
            hex_color = color
        else:
            return fallback
        
        r = hex_color[0:2].upper()
        g = hex_color[2:4].upper()
        b = hex_color[4:6].upper()
        return f"&H00{b}{g}{r}"

    @staticmethod
    async def trim_video_segment(
        input_path: str,
        output_path: str,
        start: float,
        end: float,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None
    ) -> FFMPEGResult:
        """
        Trim a video segment and normalize its format.
        
        Args:
            input_path: Path to input video
            output_path: Path for trimmed output
            start: Start time in seconds
            end: End time in seconds
            target_width: Optional output width
            target_height: Optional output height
            
        Returns:
            FFMPEGResult with operation status
        """
        if end <= start:
            return FFMPEGResult(success=False, error="End time must be greater than start time")
        
        filter_parts = []
        if target_width and target_height:
            filter_parts.append(
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease"
            )
            filter_parts.append(
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2"
            )
            filter_parts.append("setsar=1")
        
        vf_filter = ",".join(filter_parts) if filter_parts else None
        has_audio = await FFMPEGService._has_audio_stream(input_path)
        
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", input_path,
        ]
        
        if not has_audio:
            cmd.extend([
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            ])
        
        if vf_filter:
            cmd.extend(["-vf", vf_filter])
        
        cmd.extend(["-map", "0:v:0"])
        if has_audio:
            cmd.extend(["-map", "0:a:0"])
        else:
            cmd.extend(["-map", "1:a:0"])
        
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-ac", "2",
            "-ar", "44100",
            "-movflags", "+faststart",
        ])
        
        if not has_audio:
            cmd.append("-shortest")
        
        if settings.FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
        
        cmd.append(output_path)
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
        return FFMPEGResult(success=False, error=stderr)

    @staticmethod
    async def concat_segments(segment_paths: List[str], output_path: str) -> FFMPEGResult:
        """
        Concatenate multiple video segments into a single file.
        
        Args:
            segment_paths: List of segment file paths
            output_path: Path for concatenated output
            
        Returns:
            FFMPEGResult with operation status
        """
        list_path = generate_temp_path("concat_list_", ".txt")
        
        try:
            with open(list_path, "w", encoding="utf-8") as f:
                for path in segment_paths:
                    safe_path = path.replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")
            
            cmd = [
                "ffmpeg",
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                "-movflags", "+faststart",
            ]
            
            if settings.FFMPEG_THREADS > 0:
                cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
            
            cmd.append(output_path)
            
            success, stdout, stderr = await FFMPEGService.run_command(cmd)
            
            if not success:
                cleanup_file(output_path)
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", list_path,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-ac", "2",
                    "-ar", "44100",
                    "-movflags", "+faststart",
                ]
                
                if settings.FFMPEG_THREADS > 0:
                    cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
                
                cmd.append(output_path)
                
                success, stdout, stderr = await FFMPEGService.run_command(cmd)
            
            if success and os.path.exists(output_path):
                return FFMPEGResult(success=True, output_path=output_path)
            return FFMPEGResult(success=False, error=stderr)
        
        finally:
            cleanup_file(list_path)
    
    @staticmethod
    async def get_video_duration(video_path: str) -> float:
        """
        Get video duration in seconds.
        
        Args:
            video_path: Path to video file
            
        Returns:
            Duration in seconds
        """
        info = await FFMPEGService.get_video_info(video_path)
        
        # Try to get duration from format
        if "format" in info and "duration" in info["format"]:
            return float(info["format"]["duration"])
        
        # Try to get from video stream
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video" and "duration" in stream:
                return float(stream["duration"])
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not determine video duration"
        )
    
    @staticmethod
    async def add_captions_to_video(
        video_path: str,
        output_path: str,
        captions: List[dict],
        font_size: Optional[int] = None,
        font_color: str = "white",
        bg_color: Optional[str] = None,
        position: str = "bottom"
    ) -> FFMPEGResult:
        """
        Add captions/subtitles to a video.
        
        Args:
            video_path: Path to input video
            output_path: Path for output video
            captions: List of caption dicts with 'text', 'start', 'end' keys
            font_size: Font size in pixels
            font_color: Font color
            bg_color: Background color with optional opacity
            position: Caption position ('top', 'center', 'bottom')
            
        Returns:
            FFMPEGResult with operation status
        """
        # Create subtitle file in SRT format
        srt_path = output_path.rsplit(".", 1)[0] + ".srt"
        
        try:
            with open(srt_path, "w", encoding="utf-8") as f:
                for i, caption in enumerate(captions, 1):
                    start = FFMPEGService._format_srt_time(caption["start"])
                    end = FFMPEGService._format_srt_time(caption["end"])
                    text = caption["text"].replace("\n", "\\N")
                    f.write(f"{i}\n{start} --> {end}\n{text}\n\n")
            
        resolved_font_size = await FFMPEGService._resolve_font_size(video_path, font_size)
        border_width = FFMPEGService._resolve_border_width(resolved_font_size)
        alignment_map = {"top": 8, "center": 5, "bottom": 2}
        alignment = alignment_map.get(position, 2)
        
        # Build filter for subtitles
        primary_color = FFMPEGService._ass_color(font_color, ASS_COLOR_FALLBACK)
        subtitle_style = (
            f"FontName={DEFAULT_CAPTION_FONT},"
            f"FontSize={resolved_font_size},"
            f"PrimaryColour={primary_color},"
            f"OutlineColour={ASS_OUTLINE_FALLBACK},"
            f"Outline={border_width},"
            "BorderStyle=1,"
            "Shadow=0,"
            f"Alignment={alignment}"
        )
        subtitle_filter = f"subtitles='{srt_path}':force_style='{subtitle_style}'"
            
            cmd = [
                "ffmpeg",
                "-y",
                "-i", video_path,
                "-vf", subtitle_filter,
                "-c:a", "copy",
                output_path
            ]
            
            if settings.FFMPEG_THREADS > 0:
                cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
            
            success, stdout, stderr = await FFMPEGService.run_command(cmd)
            
            # Cleanup SRT file
            if os.path.exists(srt_path):
                os.remove(srt_path)
            
            if success and os.path.exists(output_path):
                return FFMPEGResult(success=True, output_path=output_path)
            else:
                return FFMPEGResult(success=False, error=stderr)
                
        except Exception as e:
            logger.error(f"Error adding captions: {e}")
            if os.path.exists(srt_path):
                os.remove(srt_path)
            return FFMPEGResult(success=False, error=str(e))
    
    @staticmethod
    async def add_text_to_image(
        image_path: str,
        output_path: str,
        text: str,
        font_size: Optional[int] = None,
        font_color: str = "white",
        bg_color: Optional[str] = None,
        position: str = "bottom",
        x_offset: int = 0,
        y_offset: int = 0
    ) -> FFMPEGResult:
        """
        Add text overlay to an image.
        
        Args:
            image_path: Path to input image
            output_path: Path for output image
            text: Text to overlay
            font_size: Font size in pixels
            font_color: Font color
            bg_color: Background box color
            position: Text position ('top', 'center', 'bottom', 'custom')
            x_offset: X offset for custom positioning
            y_offset: Y offset for custom positioning
            
        Returns:
            FFMPEGResult with operation status
        """
        # Calculate position
        if position == "top":
            x_expr = "(w-text_w)/2"
            y_expr = f"h*0.05+{y_offset}"
        elif position == "center":
            x_expr = "(w-text_w)/2"
            y_expr = f"(h-text_h)/2+{y_offset}"
        elif position == "custom":
            x_expr = str(x_offset)
            y_expr = str(y_offset)
        else:  # bottom
            x_expr = "(w-text_w)/2"
            y_expr = f"h*0.9-text_h+{y_offset}"
        
        resolved_font_size = await FFMPEGService._resolve_font_size(image_path, font_size)
        border_width = FFMPEGService._resolve_border_width(resolved_font_size)
        
        # Escape special characters in text
        escaped_text = text.replace("'", "'\\''").replace(":", "\\:")
        box_flags = "box=0"
        if bg_color:
            box_flags = f"box=1:boxcolor={bg_color}:boxborderw=10"
        
        # Build drawtext filter
        drawtext_filter = (
            f"drawtext=text='{escaped_text}':"
            f"font={DEFAULT_CAPTION_FONT}:"
            f"fontsize={resolved_font_size}:"
            f"fontcolor={font_color}:"
            f"borderw={border_width}:bordercolor=black:"
            f"x={x_expr}:y={y_expr}:"
            f"{box_flags}"
        )
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", image_path,
            "-vf", drawtext_filter,
            "-q:v", "2",
            output_path
        ]
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
        else:
            return FFMPEGResult(success=False, error=stderr)
    
    @staticmethod
    async def extract_frames(
        video_path: str,
        output_pattern: str,
        fps: float = 1.0,
        format: str = "jpg",
        quality: int = 2
    ) -> FFMPEGResult:
        """
        Extract frames from video at regular intervals.
        
        Args:
            video_path: Path to input video
            output_pattern: Output path pattern (e.g., "frame_%04d.jpg")
            fps: Frames per second to extract (e.g., 0.5 = every 2 seconds)
            format: Output image format
            quality: JPEG quality (1-31, lower is better)
            
        Returns:
            FFMPEGResult with list of output paths
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vf", f"fps={fps}",
            "-q:v", str(quality),
            output_pattern
        ]
        
        if settings.FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if not success:
            return FFMPEGResult(success=False, error=stderr)
        
        # Find all generated frames
        output_dir = os.path.dirname(output_pattern)
        pattern_base = os.path.basename(output_pattern).replace("%04d", "")
        
        frames = []
        for filename in sorted(os.listdir(output_dir)):
            if pattern_base.split(".")[0] in filename:
                frames.append(os.path.join(output_dir, filename))
        
        if frames:
            return FFMPEGResult(success=True, output_paths=frames)
        else:
            return FFMPEGResult(success=False, error="No frames extracted")
    
    @staticmethod
    async def extract_last_frame(
        video_path: str,
        output_path: str,
        quality: int = 2
    ) -> FFMPEGResult:
        """
        Extract the last frame from a video.
        
        Args:
            video_path: Path to input video
            output_path: Path for output image
            quality: JPEG quality (1-31, lower is better)
            
        Returns:
            FFMPEGResult with output path
        """
        # Get video duration
        duration = await FFMPEGService.get_video_duration(video_path)
        
        # Seek to near end and extract frame
        # Seek to 0.1 seconds before end to ensure we get the last frame
        seek_time = max(0, duration - 0.1)
        
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(seek_time),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", str(quality),
            output_path
        ]
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(
                success=True,
                output_path=output_path,
                duration=duration
            )
        else:
            return FFMPEGResult(success=False, error=stderr)
    
    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """
        Format seconds as SRT timestamp.
        
        Args:
            seconds: Time in seconds
            
        Returns:
            SRT formatted timestamp (HH:MM:SS,mmm)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# Singleton instance
ffmpeg_service = FFMPEGService()
