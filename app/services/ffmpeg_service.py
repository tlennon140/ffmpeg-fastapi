"""
FFMPEG service for video and image processing operations.
"""

import asyncio
import json
import logging
import os
import shlex
from dataclasses import dataclass
from typing import List, Optional, Tuple

from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)


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
        font_size: int = 24,
        font_color: str = "white",
        bg_color: str = "black@0.5",
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
            
            # Calculate Y position
            if position == "top":
                y_expr = "h*0.1"
            elif position == "center":
                y_expr = "(h-text_h)/2"
            else:  # bottom
                y_expr = "h*0.85"
            
            # Build filter for subtitles
            # Using drawtext for each subtitle (more control than subtitles filter)
            subtitle_filter = f"subtitles='{srt_path}':force_style='FontSize={font_size},PrimaryColour=&H00FFFFFF,BackColour=&H80000000,Alignment=2'"
            
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
        font_size: int = 24,
        font_color: str = "white",
        bg_color: str = "black@0.5",
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
        
        # Escape special characters in text
        escaped_text = text.replace("'", "'\\''").replace(":", "\\:")
        
        # Build drawtext filter
        drawtext_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={font_size}:"
            f"fontcolor={font_color}:"
            f"x={x_expr}:y={y_expr}:"
            f"box=1:boxcolor={bg_color}:boxborderw=10"
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
