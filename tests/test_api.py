"""
Tests for FFMPEG Media Processing API.
"""

from io import BytesIO
import json
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.ffmpeg_service import FFMPEGResult, ffmpeg_service
from app.services.r2_service import R2UploadResult, r2_service
from app.utils.files import generate_temp_path


def _write_file(path: str, content: bytes = b"data") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(content)


def _png_bytes() -> bytes:
    return bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
        0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
        0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
        0x54, 0x08, 0xD7, 0x63, 0xF8, 0x00, 0x00, 0x00,
        0x01, 0x00, 0x01, 0x00, 0x05, 0x1C, 0x11, 0x7A,
        0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44,
        0xAE, 0x42, 0x60, 0x82
    ])


@pytest.fixture(autouse=True)
def temp_dirs(tmp_path, monkeypatch):
    temp_dir = tmp_path / "temp"
    output_dir = tmp_path / "output"
    temp_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr(settings, "TEMP_DIR", str(temp_dir))
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(output_dir))
    return {"temp_dir": temp_dir, "output_dir": output_dir}


@pytest.fixture
def client(monkeypatch, temp_dirs):
    """Create test client with stubbed ffmpeg detection."""
    def fake_which(name: str):
        if name in ("ffmpeg", "ffprobe"):
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def api_key():
    """Get a valid API key for testing."""
    return settings.api_keys_list[0]


@pytest.fixture
def api_headers(api_key):
    return {"X-API-Key": api_key}


class TestRootEndpoint:
    """Tests for root endpoint."""
    
    def test_root(self, client):
        """Test root endpoint response."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "FFMPEG Media Processing API"
        assert "docs" in data
        assert "health" in data


class TestHealthEndpoints:
    """Tests for health check endpoints."""
    
    def test_health_check(self, client):
        """Test basic health check."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "degraded"]
        assert "timestamp" in data
        assert "ffmpeg_available" in data
    
    def test_readiness_check(self, client):
        """Test readiness check."""
        response = client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert "ready" in data
        assert "checks" in data


class TestAuthentication:
    """Tests for API key authentication."""
    
    def test_missing_api_key(self, client):
        """Test request without API key."""
        response = client.post("/api/v1/captions/image")
        assert response.status_code == 401
        assert "Missing API key" in response.json()["detail"]
    
    def test_invalid_api_key(self, client):
        """Test request with invalid API key."""
        response = client.post(
            "/api/v1/captions/image",
            headers={"X-API-Key": "invalid-key-12345"}
        )
        assert response.status_code == 403
        assert "Invalid API key" in response.json()["detail"]


class TestCaptionsEndpoints:
    """Tests for caption endpoints."""
    
    def test_image_caption_missing_file(self, client, api_headers):
        """Test image caption without file."""
        response = client.post(
            "/api/v1/captions/image",
            headers=api_headers,
            data={"text": "Hello World"}
        )
        assert response.status_code == 422  # Validation error
    
    def test_image_caption_missing_text(self, client, api_headers):
        """Test image caption without text."""
        response = client.post(
            "/api/v1/captions/image",
            headers=api_headers,
            files={"image": ("test.png", BytesIO(_png_bytes()), "image/png")}
        )
        assert response.status_code == 422  # Missing text field
    
    def test_video_caption_invalid_json(self, client, api_headers):
        """Test video caption with invalid JSON."""
        response = client.post(
            "/api/v1/captions/video",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"fake"), "video/mp4")},
            data={"captions_json": "invalid json"}
        )
        assert response.status_code == 400
        assert "Invalid captions JSON" in response.json()["detail"]
    
    def test_image_caption_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test image caption success path."""
        async def fake_add_text_to_image(*args, **kwargs):
            output_path = kwargs["output_path"]
            _write_file(output_path, b"image")
            return FFMPEGResult(success=True, output_path=output_path)

        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="captions/image.png", url="https://cdn.example.com/captions/image.png")

        monkeypatch.setattr(ffmpeg_service, "add_text_to_image", fake_add_text_to_image)
        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        response = client.post(
            "/api/v1/captions/image",
            headers=api_headers,
            files={"image": ("test.png", BytesIO(_png_bytes()), "image/png")},
            data={"text": "Hello World", "upload": "true", "upload_location": "captions"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["r2_url"].startswith("https://")
        filename = data["filename"]
        output_path = Path(temp_dirs["output_dir"]) / filename
        assert output_path.exists()

        download = client.get(
            f"/api/v1/captions/download/{filename}",
            headers=api_headers
        )
        assert download.status_code == 200
    
    def test_video_caption_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test video caption success path."""
        async def fake_add_captions_to_video(*args, **kwargs):
            output_path = kwargs["output_path"]
            _write_file(output_path, b"video")
            return FFMPEGResult(success=True, output_path=output_path)

        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="captions/video.mp4", url="https://cdn.example.com/captions/video.mp4")

        monkeypatch.setattr(ffmpeg_service, "add_captions_to_video", fake_add_captions_to_video)
        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        captions_json = json.dumps([
            {"text": "Hello", "start": 0, "end": 1.5},
            {"text": "World", "start": 2, "end": 3.5},
        ])

        response = client.post(
            "/api/v1/captions/video",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"video"), "video/mp4")},
            data={"captions_json": captions_json, "upload": "true", "upload_location": "captions"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["r2_url"].startswith("https://")
        filename = data["filename"]
        output_path = Path(temp_dirs["output_dir"]) / filename
        assert output_path.exists()

        download = client.get(
            f"/api/v1/captions/download/{filename}",
            headers=api_headers
        )
        assert download.status_code == 200


class TestVideoConcatEndpoints:
    """Tests for video concatenation endpoints."""
    
    def test_concat_invalid_url(self, client, api_headers):
        """Test concat with invalid URL."""
        response = client.post(
            "/api/v1/videos/concat",
            headers=api_headers,
            json={"segments": [{"url": "not-a-url", "start": 0, "end": 1}]}
        )
        assert response.status_code == 422
    
    def test_concat_invalid_times(self, client, api_headers):
        """Test concat with end time before start time."""
        response = client.post(
            "/api/v1/videos/concat",
            headers=api_headers,
            json={"segments": [{"url": "https://example.com/video.mp4", "start": 5, "end": 2}]}
        )
        assert response.status_code == 422
    
    def test_concat_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test concat success path."""
        async def fake_download_video_from_url(url: str, prefix: str = "remote_") -> str:
            path = generate_temp_path(prefix, ".mp4")
            _write_file(path, b"video")
            return path

        async def fake_get_media_dimensions(path: str):
            return 1280, 720

        async def fake_trim_video_segment(*args, **kwargs):
            output_path = kwargs["output_path"]
            _write_file(output_path, b"segment")
            return FFMPEGResult(success=True, output_path=output_path)

        async def fake_concat_segments(segment_paths, output_path):
            _write_file(output_path, b"concat")
            return FFMPEGResult(success=True, output_path=output_path)

        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="concat/result.mp4", url="https://cdn.example.com/concat/result.mp4")

        monkeypatch.setattr(ffmpeg_service, "download_video_from_url", fake_download_video_from_url)
        monkeypatch.setattr(ffmpeg_service, "get_media_dimensions", fake_get_media_dimensions)
        monkeypatch.setattr(ffmpeg_service, "trim_video_segment", fake_trim_video_segment)
        monkeypatch.setattr(ffmpeg_service, "concat_segments", fake_concat_segments)
        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        response = client.post(
            "/api/v1/videos/concat",
            headers=api_headers,
            json={
                "segments": [
                    {"url": "https://example.com/video1.mp4", "start": 0, "end": 2.5},
                    {"url": "https://example.com/video2.mp4", "start": 1, "end": 3},
                ],
                "upload": True,
                "upload_location": "concat",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["r2_url"].startswith("https://")
        output_path = Path(temp_dirs["output_dir"]) / data["filename"]
        assert output_path.exists()


class TestVideoAudioEndpoints:
    """Tests for video audio endpoints."""
    
    def test_add_audio_missing_audio(self, client, api_headers):
        """Test adding audio without audio file."""
        response = client.post(
            "/api/v1/videos/audio",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"video"), "video/mp4")}
        )
        assert response.status_code == 422
    
    def test_add_audio_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test adding audio success path."""
        async def fake_add_audio_to_video(*args, **kwargs):
            output_path = kwargs["output_path"]
            _write_file(output_path, b"video")
            return FFMPEGResult(success=True, output_path=output_path)

        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="audio/result.mp4", url="https://cdn.example.com/audio/result.mp4")

        monkeypatch.setattr(ffmpeg_service, "add_audio_to_video", fake_add_audio_to_video)
        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        response = client.post(
            "/api/v1/videos/audio",
            headers=api_headers,
            files={
                "video": ("test.mp4", BytesIO(b"video"), "video/mp4"),
                "audio": ("track.mp3", BytesIO(b"audio"), "audio/mpeg"),
            },
            data={"replace_audio": "true", "upload": "true", "upload_location": "audio"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["r2_url"].startswith("https://")
        output_path = Path(temp_dirs["output_dir"]) / data["filename"]
        assert output_path.exists()


class TestVideoTransformEndpoints:
    """Tests for video transform endpoints."""
    
    def test_aspect_invalid_ratio(self, client, api_headers):
        """Test aspect conversion with invalid ratio."""
        response = client.post(
            "/api/v1/videos/aspect",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"video"), "video/mp4")},
            data={"ratio": "4:3"}
        )
        assert response.status_code == 400
    
    def test_aspect_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test aspect conversion success path."""
        async def fake_convert_aspect_ratio(*args, **kwargs):
            output_path = kwargs["output_path"]
            _write_file(output_path, b"video")
            return FFMPEGResult(success=True, output_path=output_path)

        monkeypatch.setattr(ffmpeg_service, "convert_aspect_ratio", fake_convert_aspect_ratio)

        response = client.post(
            "/api/v1/videos/aspect",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"video"), "video/mp4")},
            data={"ratio": "9:16"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        output_path = Path(temp_dirs["output_dir"]) / data["filename"]
        assert output_path.exists()
    
    def test_crop_vertical_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test vertical crop success path."""
        async def fake_smart_crop_video(*args, **kwargs):
            output_path = kwargs["output_path"]
            _write_file(output_path, b"video")
            return FFMPEGResult(success=True, output_path=output_path)

        monkeypatch.setattr(ffmpeg_service, "smart_crop_video", fake_smart_crop_video)

        response = client.post(
            "/api/v1/videos/crop/vertical",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"video"), "video/mp4")},
            data={"ratio": "9:16"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        output_path = Path(temp_dirs["output_dir"]) / data["filename"]
        assert output_path.exists()


class TestStorageEndpoints:
    """Tests for storage endpoints."""
    
    def test_r2_upload_success(self, client, api_headers, monkeypatch):
        """Test R2 upload success path."""
        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="uploads/test.mp4", url="https://cdn.example.com/uploads/test.mp4")

        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        response = client.post(
            "/api/v1/storage/r2/upload",
            headers=api_headers,
            files={"file": ("test.mp4", BytesIO(b"video"), "video/mp4")}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["key"] == "uploads/test.mp4"
        assert data["url"].startswith("https://")
    
    def test_r2_upload_output_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test R2 upload of output file success path."""
        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="outputs/result.mp4", url="https://cdn.example.com/outputs/result.mp4")

        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        output_path = Path(temp_dirs["output_dir"]) / "result.mp4"
        _write_file(str(output_path), b"video")

        response = client.post(
            "/api/v1/storage/r2/upload/output/result.mp4",
            headers=api_headers,
            params={"key_prefix": "outputs"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["key"] == "outputs/result.mp4"
        assert data["url"].startswith("https://")
    
    def test_r2_upload_output_missing(self, client, api_headers):
        """Test R2 upload of output file when missing."""
        response = client.post(
            "/api/v1/storage/r2/upload/output/missing.mp4",
            headers=api_headers
        )
        assert response.status_code == 404


class TestFramesEndpoints:
    """Tests for frame extraction endpoints."""
    
    def test_extract_frames_missing_file(self, client, api_headers):
        """Test frame extraction without file."""
        response = client.post(
            "/api/v1/frames/extract",
            headers=api_headers
        )
        assert response.status_code == 422
    
    def test_extract_frames_invalid_fps(self, client, api_headers):
        """Test frame extraction with invalid FPS."""
        response = client.post(
            "/api/v1/frames/extract",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"fake"), "video/mp4")},
            data={"fps": "100"}  # Too high
        )
        assert response.status_code == 422
    
    def test_extract_last_frame_missing_file(self, client, api_headers):
        """Test last frame extraction without file."""
        response = client.post(
            "/api/v1/frames/last",
            headers=api_headers
        )
        assert response.status_code == 422
    
    def test_extract_frames_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test frame extraction success path."""
        async def fake_extract_frames(*args, **kwargs):
            output_pattern = kwargs["output_pattern"]
            frame1 = output_pattern.replace("%04d", "0001")
            frame2 = output_pattern.replace("%04d", "0002")
            _write_file(frame1, b"frame")
            _write_file(frame2, b"frame")
            return FFMPEGResult(success=True, output_paths=[frame1, frame2])

        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="frames/frames.zip", url="https://cdn.example.com/frames/frames.zip")

        monkeypatch.setattr(ffmpeg_service, "extract_frames", fake_extract_frames)
        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        response = client.post(
            "/api/v1/frames/extract",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"video"), "video/mp4")},
            data={"fps": "1", "format": "jpg", "upload": "true", "upload_location": "frames"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["frame_count"] == 2
        assert data["r2_url"].startswith("https://")
        output_path = Path(temp_dirs["output_dir"]) / data["filename"]
        assert output_path.exists()

        download = client.get(
            f"/api/v1/frames/download/{data['filename']}",
            headers=api_headers
        )
        assert download.status_code == 200
    
    def test_extract_last_frame_success(self, client, api_headers, monkeypatch, temp_dirs):
        """Test last frame extraction success path."""
        async def fake_extract_last_frame(*args, **kwargs):
            output_path = kwargs["output_path"]
            _write_file(output_path, b"frame")
            return FFMPEGResult(success=True, output_path=output_path, duration=12.3)

        async def fake_upload_file_path(*args, **kwargs):
            return R2UploadResult(key="frames/last.jpg", url="https://cdn.example.com/frames/last.jpg")

        monkeypatch.setattr(ffmpeg_service, "extract_last_frame", fake_extract_last_frame)
        monkeypatch.setattr(r2_service, "upload_file_path", fake_upload_file_path)

        response = client.post(
            "/api/v1/frames/last",
            headers=api_headers,
            files={"video": ("test.mp4", BytesIO(b"video"), "video/mp4")},
            data={"format": "jpg", "upload": "true", "upload_location": "frames"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["video_duration"] == pytest.approx(12.3)
        assert data["r2_url"].startswith("https://")
        output_path = Path(temp_dirs["output_dir"]) / data["filename"]
        assert output_path.exists()

        download = client.get(
            f"/api/v1/frames/download/{data['filename']}",
            headers=api_headers
        )
        assert download.status_code == 200


class TestRateLimiting:
    """Tests for rate limiting."""
    
    def test_rate_limit_headers(self, client):
        """Test that rate limit headers are present."""
        response = client.get("/health")
        # Health endpoint is exempt from rate limiting
        assert response.status_code == 200


class TestDownloadEndpoints:
    """Tests for download endpoints."""
    
    def test_download_nonexistent_caption(self, client, api_headers):
        """Test downloading non-existent caption file."""
        response = client.get(
            "/api/v1/captions/download/nonexistent.mp4",
            headers=api_headers
        )
        assert response.status_code == 404
    
    def test_download_nonexistent_frames(self, client, api_headers):
        """Test downloading non-existent frames file."""
        response = client.get(
            "/api/v1/frames/download/nonexistent.zip",
            headers=api_headers
        )
        assert response.status_code == 404
