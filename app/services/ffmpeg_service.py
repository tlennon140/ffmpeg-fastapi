"""
FFMPEG service for video and image processing operations.
"""

import asyncio
import json
import logging
import os
import shlex
import textwrap
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
DEFAULT_CAPTION_FONT_RATIO = 0.016
DEFAULT_CAPTION_MIN_FONT_SIZE = 8
DEFAULT_CAPTION_MAX_FONT_SIZE = 32
DEFAULT_CAPTION_BORDER_RATIO = 0.12
DEFAULT_CAPTION_MIN_BORDER = 2
DEFAULT_CAPTION_MAX_BORDER = 10
DEFAULT_CAPTION_MAX_WIDTH_RATIO = 0.72
DEFAULT_CAPTION_LINE_HEIGHT = 1.2
DEFAULT_CAPTION_SIDE_MARGIN_RATIO = 0.06
DEFAULT_CAPTION_BOTTOM_MARGIN_RATIO = 0.12
DEFAULT_CAPTION_BOX_ALPHA = 0.65
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
    def _resolve_font_size_from_height(height: int, font_size: Optional[int]) -> int:
        """Resolve font size based on a known media height."""
        if font_size is not None:
            return font_size
        
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
    def _resolve_box_padding(font_size: int) -> int:
        """Resolve box padding around captions."""
        scaled_padding = int(round(font_size * 0.7))
        return max(10, min(scaled_padding, 20))

    @staticmethod
    def _resolve_line_spacing(font_size: int) -> int:
        """Resolve line spacing in pixels."""
        return max(0, int(round(font_size * (DEFAULT_CAPTION_LINE_HEIGHT - 1.0))))

    @staticmethod
    def _wrap_caption_text(text: str, max_chars: int) -> str:
        """Wrap caption text to a maximum number of characters per line."""
        cleaned = " ".join(text.split())
        if not cleaned:
            return ""
        
        lines = textwrap.wrap(
            cleaned,
            width=max_chars,
            break_long_words=True,
            break_on_hyphens=False
        )
        if len(lines) <= 2:
            return "\n".join(lines)
        
        remainder = " ".join(lines[1:])
        last_line = textwrap.shorten(remainder, width=max_chars, placeholder="...")
        return "\n".join([lines[0], last_line])

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
    def _parse_alpha(value: str) -> Optional[float]:
        """Parse alpha values like 0.5 or 50% into a 0-1 float."""
        if not value:
            return None
        value = value.strip()
        try:
            if value.endswith("%"):
                return float(value[:-1]) / 100.0
            alpha = float(value)
        except ValueError:
            return None
        if alpha > 1.0 and alpha <= 100.0:
            alpha = alpha / 100.0
        if 0.0 <= alpha <= 1.0:
            return alpha
        return None

    @staticmethod
    def _split_color_alpha(color: str) -> Tuple[str, Optional[float]]:
        """Split colors like 'black@0.65' into base color and alpha."""
        if not color:
            return "", None
        color = color.strip()
        if "@" not in color:
            return color, None
        base, alpha_value = color.rsplit("@", 1)
        return base.strip(), FFMPEGService._parse_alpha(alpha_value)

    @staticmethod
    def _ass_color_with_alpha(color: str, fallback: str) -> str:
        """Convert color with optional alpha to ASS format (&HAABBGGRR)."""
        base_color, alpha = FFMPEGService._split_color_alpha(color)
        color_value = FFMPEGService._ass_color(base_color, fallback)
        if alpha is None:
            return color_value
        alpha = max(0.0, min(alpha, 1.0))
        alpha_byte = int(round(alpha * 255))
        return f"&H{alpha_byte:02X}{color_value[4:]}"

    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        """Format seconds as ASS time (H:MM:SS.CS)."""
        total_cs = int(round(max(0.0, seconds) * 100))
        cs = total_cs % 100
        total_seconds = total_cs // 100
        secs = total_seconds % 60
        total_minutes = total_seconds // 60
        mins = total_minutes % 60
        hours = total_minutes // 60
        return f"{hours}:{mins:02d}:{secs:02d}.{cs:02d}"

    @staticmethod
    def _ass_escape_text(value: str) -> str:
        """Escape ASS dialogue text and preserve new lines."""
        escaped = value.replace("\\", "\\\\")
        escaped = escaped.replace("{", "\\{").replace("}", "\\}")
        escaped = escaped.replace("\r", "")
        return escaped.replace("\n", "\\N")

    @staticmethod
    def _escape_drawtext_text(value: str) -> str:
        """Escape drawtext values for FFmpeg filter syntax."""
        escaped = []
        for ch in value:
            if ch == "\\":
                escaped.append("\\\\")
            elif ch == "\n":
                escaped.append("\\n")
            elif ch == "\r":
                continue
            elif ch == ":":
                escaped.append("\\:")
            elif ch == "'":
                escaped.append("\\'")
            elif ch == ",":
                escaped.append("\\,")
            elif ch in {"[", "]"}:
                escaped.append(f"\\{ch}")
            elif ch == "%":
                escaped.append("\\%")
            else:
                escaped.append(ch)
        return "".join(escaped)

    @staticmethod
    def _find_font_file(font_name: str) -> Optional[str]:
        """Find a font file in the configured font folder."""
        folder = settings.CAPTION_FONT_FOLDER
        if not folder or not os.path.isdir(folder):
            return None
        
        target_name = Path(font_name).stem.lower()
        for entry in os.listdir(folder):
            entry_path = Path(entry)
            if entry_path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            if entry_path.stem.lower() == target_name:
                return os.path.join(folder, entry)
        return None

    @staticmethod
    def _drawtext_font_spec() -> str:
        """Build drawtext font parameter using config settings."""
        font_name = settings.CAPTION_FONT or DEFAULT_CAPTION_FONT
        font_file = FFMPEGService._find_font_file(font_name)
        if font_file:
            fontfile = FFMPEGService._escape_drawtext_text(font_file)
            return f"fontfile='{fontfile}':"
        font_name = FFMPEGService._escape_drawtext_text(font_name)
        return f"font='{font_name}':"

    @staticmethod
    def _parse_aspect_ratio(value: str) -> float:
        """Parse aspect ratio strings like '9:16'."""
        ratios = {
            "9:16": 9 / 16,
            "1:1": 1.0,
            "16:9": 16 / 9,
        }
        if value not in ratios:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aspect ratio must be one of: 9:16, 1:1, 16:9"
            )
        return ratios[value]

    @staticmethod
    def _even(value: int) -> int:
        """Ensure value is even and at least 2."""
        value = max(2, value)
        return value if value % 2 == 0 else value - 1

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
        subtitle_path = generate_temp_path("captions_", ".ass")
        try:
            logger.info(
                "Captioning video: input=%s output=%s captions=%d font_size=%s "
                "font_color=%s bg_color=%s position=%s",
                video_path,
                output_path,
                len(captions),
                font_size,
                font_color,
                bg_color,
                position,
            )
            width, height = await FFMPEGService.get_media_dimensions(video_path)
            resolved_font_size = FFMPEGService._resolve_font_size_from_height(height, font_size)
            border_width = FFMPEGService._resolve_border_width(resolved_font_size)
            box_padding = FFMPEGService._resolve_box_padding(resolved_font_size)
            max_text_width = int(round(width * DEFAULT_CAPTION_MAX_WIDTH_RATIO))
            min_side_margin = int(round(width * DEFAULT_CAPTION_SIDE_MARGIN_RATIO))
            bottom_margin = int(round(height * DEFAULT_CAPTION_BOTTOM_MARGIN_RATIO))
            max_chars = max(1, int(max_text_width / (resolved_font_size * 0.6)))
            
            if position == "top":
                alignment = 8
                margin_v = int(round(height * 0.06))
            elif position == "center":
                alignment = 5
                margin_v = 0
            else:  # bottom
                alignment = 2
                margin_v = bottom_margin

            logger.info(
                "Caption layout: size=%dx%d font_size=%d max_chars=%d alignment=%d margin_v=%d",
                width,
                height,
                resolved_font_size,
                max_chars,
                alignment,
                margin_v,
            )
            
            resolved_bg_color = None
            if bg_color is not None:
                resolved_bg_color = bg_color.strip()
            
            primary_color = FFMPEGService._ass_color_with_alpha(
                font_color,
                ASS_COLOR_FALLBACK
            )
            outline_color = ASS_OUTLINE_FALLBACK
            border_style = 1
            outline_size = border_width
            back_color = "&H00000000"
            if resolved_bg_color:
                border_style = 3
                outline_size = box_padding
                back_color = FFMPEGService._ass_color_with_alpha(
                    resolved_bg_color,
                    ASS_OUTLINE_FALLBACK
                )
            
            font_name = settings.CAPTION_FONT or DEFAULT_CAPTION_FONT
            font_file = FFMPEGService._find_font_file(font_name)
            if font_file:
                font_name = Path(font_file).stem

            logger.info(
                "Caption font resolved: name=%s file=%s",
                font_name,
                font_file or "none",
            )
            
            ass_lines = [
                "[Script Info]",
                "ScriptType: v4.00+",
                f"PlayResX: {width}",
                f"PlayResY: {height}",
                "ScaledBorderAndShadow: yes",
                "",
                "[V4+ Styles]",
                (
                    "Format: Name,Fontname,Fontsize,PrimaryColour,"
                    "SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
                    "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
                    "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,"
                    "MarginV,Encoding"
                ),
                (
                    "Style: Default,"
                    f"{font_name},{resolved_font_size},{primary_color},"
                    f"{primary_color},{outline_color},{back_color},"
                    "0,0,0,0,100,100,0,0,"
                    f"{border_style},{outline_size},0,{alignment},"
                    f"{min_side_margin},{min_side_margin},{margin_v},1"
                ),
                "",
                "[Events]",
                (
                    "Format: Layer,Start,End,Style,Name,MarginL,MarginR,"
                    "MarginV,Effect,Text"
                ),
            ]

            dialog_count = 0
            skipped_empty = 0
            skipped_time = 0
            for caption in captions:
                text = FFMPEGService._wrap_caption_text(str(caption["text"]), max_chars)
                if not text:
                    skipped_empty += 1
                    continue
                start = float(caption["start"])
                end = float(caption["end"])
                if end <= start:
                    skipped_time += 1
                    continue
                ass_text = FFMPEGService._ass_escape_text(text)
                ass_lines.append(
                    "Dialogue: 0,"
                    f"{FFMPEGService._format_ass_time(start)},"
                    f"{FFMPEGService._format_ass_time(end)},"
                    "Default,,0,0,0,,"
                    f"{ass_text}"
                )
                dialog_count += 1
            
            with open(subtitle_path, "w", encoding="utf-8") as subtitle_file:
                subtitle_file.write("\n".join(ass_lines) + "\n")

            logger.info(
                "ASS captions written: file=%s dialogues=%d skipped_empty=%d skipped_time=%d",
                subtitle_path,
                dialog_count,
                skipped_empty,
                skipped_time,
            )
            
            subtitle_filter = f"subtitles=filename='{subtitle_path}':charenc=UTF-8"
            fonts_dir = settings.CAPTION_FONT_FOLDER
            if fonts_dir and os.path.isdir(fonts_dir):
                subtitle_filter += f":fontsdir='{os.path.abspath(fonts_dir)}'"

            logger.info(
                "Subtitle filter: %s",
                subtitle_filter,
            )
            
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

            if success and os.path.exists(output_path):
                logger.info("Captioning complete: output=%s", output_path)
                return FFMPEGResult(success=True, output_path=output_path)

            stderr_summary = (stderr or "").strip()
            if stderr_summary:
                logger.error("FFMPEG captioning failed: %s", stderr_summary[:2000])
            return FFMPEGResult(success=False, error=stderr)
                
        except Exception as e:
            logger.exception("Error adding captions")
            return FFMPEGResult(success=False, error=str(e))
        finally:
            cleanup_file(subtitle_path)
    
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
        escaped_text = FFMPEGService._escape_drawtext_text(text)
        box_flags = "box=0"
        if bg_color:
            box_flags = f"box=1:boxcolor={bg_color}:boxborderw=10"
        
        # Build drawtext filter
        drawtext_filter = (
            f"drawtext=text='{escaped_text}':"
            f"{FFMPEGService._drawtext_font_spec()}"
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
    async def add_audio_to_video(
        video_path: str,
        audio_path: str,
        output_path: str,
        replace_audio: bool = False
    ) -> FFMPEGResult:
        """
        Add or replace audio on a video.
        
        Args:
            video_path: Path to input video
            audio_path: Path to input audio
            output_path: Path for output video
            replace_audio: If True, replace original audio
            
        Returns:
            FFMPEGResult with operation status
        """
        try:
            has_audio = await FFMPEGService._has_audio_stream(video_path)
        except HTTPException as exc:
            return FFMPEGResult(success=False, error=str(exc.detail))
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-i", audio_path,
        ]
        
        if replace_audio or not has_audio:
            cmd.extend([
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                "-movflags", "+faststart",
            ])
        else:
            cmd.extend([
                "-filter_complex",
                "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=2[aout]",
                "-map", "0:v:0",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                "-movflags", "+faststart",
            ])
        
        if settings.FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
        
        cmd.append(output_path)
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
        return FFMPEGResult(success=False, error=stderr)

    @staticmethod
    async def add_watermark_to_video(
        video_path: str,
        logo_path: str,
        output_path: str,
        position: str = "top-right",
        scale_ratio: float = 0.18,
        opacity: float = 0.9,
        margin_ratio: float = 0.04
    ) -> FFMPEGResult:
        """
        Overlay a logo watermark on a video.
        
        Args:
            video_path: Path to input video
            logo_path: Path to logo image
            output_path: Path for output video
            position: Overlay position
            scale_ratio: Logo width ratio relative to video width
            opacity: Logo opacity (0-1)
            margin_ratio: Margin ratio relative to video width/height
            
        Returns:
            FFMPEGResult with operation status
        """
        width, height = await FFMPEGService.get_media_dimensions(video_path)
        margin_x = int(round(width * margin_ratio))
        margin_y = int(round(height * margin_ratio))
        
        position_map = {
            "top-left": (f"{margin_x}", f"{margin_y}"),
            "top-right": (f"w-overlay_w-{margin_x}", f"{margin_y}"),
            "bottom-left": (f"{margin_x}", f"h-overlay_h-{margin_y}"),
            "bottom-right": (f"w-overlay_w-{margin_x}", f"h-overlay_h-{margin_y}"),
            "center": ("(w-overlay_w)/2", "(h-overlay_h)/2"),
        }
        x_expr, y_expr = position_map.get(position, position_map["top-right"])
        
        scale_ratio = max(0.05, min(scale_ratio, 0.5))
        opacity = max(0.0, min(opacity, 1.0))
        
        filter_complex = (
            f"[1:v][0:v]scale2ref=w=main_w*{scale_ratio}:h=-1[logo][base];"
            f"[logo]format=rgba,colorchannelmixer=aa={opacity}[logo_alpha];"
            f"[base][logo_alpha]overlay={x_expr}:{y_expr}"
        )
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-i", logo_path,
            "-filter_complex", filter_complex,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
        ]
        
        if settings.FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
        
        cmd.append(output_path)
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
        return FFMPEGResult(success=False, error=stderr)

    @staticmethod
    async def _normalize_video_clip(
        input_path: str,
        output_path: str,
        target_width: int,
        target_height: int
    ) -> FFMPEGResult:
        """Normalize a video clip to a target size with audio."""
        has_audio = await FFMPEGService._has_audio_stream(input_path)
        vf_filter = (
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,"
            "setsar=1"
        )
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_path,
        ]
        
        if not has_audio:
            cmd.extend([
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            ])
        
        cmd.extend(["-vf", vf_filter, "-map", "0:v:0"])
        if has_audio:
            cmd.extend(["-map", "0:a:0"])
        else:
            cmd.extend(["-map", "1:a:0", "-shortest"])
        
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-ac", "2",
            "-ar", "44100",
            "-movflags", "+faststart",
        ])
        
        if settings.FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
        
        cmd.append(output_path)
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
        return FFMPEGResult(success=False, error=stderr)

    @staticmethod
    async def append_intro_outro(
        video_path: str,
        output_path: str,
        intro_path: Optional[str] = None,
        outro_path: Optional[str] = None
    ) -> FFMPEGResult:
        """
        Append intro/outro clips to a video.
        
        Args:
            video_path: Path to main video
            output_path: Path for output video
            intro_path: Optional intro video path
            outro_path: Optional outro video path
            
        Returns:
            FFMPEGResult with operation status
        """
        inputs = [p for p in [intro_path, video_path, outro_path] if p]
        if len(inputs) == 1:
            return FFMPEGResult(success=False, error="No intro or outro provided")
        
        width, height = await FFMPEGService.get_media_dimensions(video_path)
        normalized_paths: List[str] = []
        
        try:
            for path in inputs:
                normalized_path = generate_temp_path("append_", ".mp4")
                normalized_paths.append(normalized_path)
                result = await FFMPEGService._normalize_video_clip(
                    input_path=path,
                    output_path=normalized_path,
                    target_width=width,
                    target_height=height
                )
                if not result.success:
                    return FFMPEGResult(success=False, error=result.error)
            
            concat_result = await FFMPEGService.concat_segments(normalized_paths, output_path)
            if concat_result.success:
                return concat_result
            return FFMPEGResult(success=False, error=concat_result.error)
        
        finally:
            for path in normalized_paths:
                cleanup_file(path)

    @staticmethod
    async def extract_audio_from_video(
        video_path: str,
        output_path: str,
        format: str = "mp3"
    ) -> FFMPEGResult:
        """
        Extract audio from a video.
        
        Args:
            video_path: Path to input video
            output_path: Path for output audio
            format: Output audio format
            
        Returns:
            FFMPEGResult with operation status
        """
        codecs = {
            "mp3": ("libmp3lame", ["-q:a", "2"]),
            "wav": ("pcm_s16le", []),
            "aac": ("aac", []),
            "m4a": ("aac", []),
            "ogg": ("libvorbis", []),
            "flac": ("flac", []),
        }
        codec, extra_args = codecs.get(format, (None, None))
        if codec is None:
            return FFMPEGResult(success=False, error="Unsupported audio format")
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vn",
            "-acodec", codec,
        ]
        cmd.extend(extra_args)
        cmd.append(output_path)
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
        return FFMPEGResult(success=False, error=stderr)

    @staticmethod
    async def convert_aspect_ratio(
        video_path: str,
        output_path: str,
        target_ratio: str,
        background_color: str = "black"
    ) -> FFMPEGResult:
        """
        Convert a video's aspect ratio using padding.
        
        Args:
            video_path: Path to input video
            output_path: Path for output video
            target_ratio: Target ratio string (9:16, 1:1, 16:9)
            background_color: Padding color
            
        Returns:
            FFMPEGResult with operation status
        """
        ratio = FFMPEGService._parse_aspect_ratio(target_ratio)
        width, height = await FFMPEGService.get_media_dimensions(video_path)
        
        if ratio >= 1:
            target_width = width
            target_height = int(round(width / ratio))
        else:
            target_height = height
            target_width = int(round(height * ratio))
        
        target_width = FFMPEGService._even(target_width)
        target_height = FFMPEGService._even(target_height)
        
        vf_filter = (
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color={background_color}"
        )
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vf", vf_filter,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
        ]
        
        if settings.FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
        
        cmd.append(output_path)
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
        return FFMPEGResult(success=False, error=stderr)

    @staticmethod
    async def smart_crop_video(
        video_path: str,
        output_path: str,
        target_ratio: str = "9:16"
    ) -> FFMPEGResult:
        """
        Crop a video to a target aspect ratio.
        
        Args:
            video_path: Path to input video
            output_path: Path for output video
            target_ratio: Target ratio string (9:16, 1:1, 16:9)
            
        Returns:
            FFMPEGResult with operation status
        """
        ratio = FFMPEGService._parse_aspect_ratio(target_ratio)
        width, height = await FFMPEGService.get_media_dimensions(video_path)
        input_ratio = width / height
        
        if input_ratio >= ratio:
            crop_width = int(round(height * ratio))
            crop_height = height
            x_offset = int(round((width - crop_width) / 2))
            y_offset = 0
        else:
            crop_width = width
            crop_height = int(round(width / ratio))
            x_offset = 0
            y_offset = int(round((height - crop_height) / 2))
        
        crop_width = FFMPEGService._even(crop_width)
        crop_height = FFMPEGService._even(crop_height)
        x_offset = max(0, x_offset)
        y_offset = max(0, y_offset)
        
        vf_filter = f"crop={crop_width}:{crop_height}:{x_offset}:{y_offset}"
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vf", vf_filter,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "copy",
            "-movflags", "+faststart",
        ]
        
        if settings.FFMPEG_THREADS > 0:
            cmd.extend(["-threads", str(settings.FFMPEG_THREADS)])
        
        cmd.append(output_path)
        
        success, stdout, stderr = await FFMPEGService.run_command(cmd)
        
        if success and os.path.exists(output_path):
            return FFMPEGResult(success=True, output_path=output_path)
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
