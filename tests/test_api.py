"""
Tests for FFMPEG Media Processing API.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def api_key():
    """Get a valid API key for testing."""
    return settings.api_keys_list[0]


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
    
    def test_image_caption_missing_file(self, client, api_key):
        """Test image caption without file."""
        response = client.post(
            "/api/v1/captions/image",
            headers={"X-API-Key": api_key},
            data={"text": "Hello World"}
        )
        assert response.status_code == 422  # Validation error
    
    def test_image_caption_missing_text(self, client, api_key):
        """Test image caption without text."""
        # Create a minimal test image
        from io import BytesIO
        
        # 1x1 PNG pixel
        png_data = bytes([
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
        
        response = client.post(
            "/api/v1/captions/image",
            headers={"X-API-Key": api_key},
            files={"image": ("test.png", BytesIO(png_data), "image/png")}
        )
        assert response.status_code == 422  # Missing text field
    
    def test_video_caption_invalid_json(self, client, api_key):
        """Test video caption with invalid JSON."""
        from io import BytesIO
        
        response = client.post(
            "/api/v1/captions/video",
            headers={"X-API-Key": api_key},
            files={"video": ("test.mp4", BytesIO(b"fake"), "video/mp4")},
            data={"captions_json": "invalid json"}
        )
        assert response.status_code == 400
        assert "Invalid captions JSON" in response.json()["detail"]


class TestFramesEndpoints:
    """Tests for frame extraction endpoints."""
    
    def test_extract_frames_missing_file(self, client, api_key):
        """Test frame extraction without file."""
        response = client.post(
            "/api/v1/frames/extract",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 422
    
    def test_extract_frames_invalid_fps(self, client, api_key):
        """Test frame extraction with invalid FPS."""
        from io import BytesIO
        
        response = client.post(
            "/api/v1/frames/extract",
            headers={"X-API-Key": api_key},
            files={"video": ("test.mp4", BytesIO(b"fake"), "video/mp4")},
            data={"fps": "100"}  # Too high
        )
        assert response.status_code == 422
    
    def test_extract_last_frame_missing_file(self, client, api_key):
        """Test last frame extraction without file."""
        response = client.post(
            "/api/v1/frames/last",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 422


class TestRateLimiting:
    """Tests for rate limiting."""
    
    def test_rate_limit_headers(self, client):
        """Test that rate limit headers are present."""
        response = client.get("/health")
        # Health endpoint is exempt from rate limiting
        assert response.status_code == 200


class TestDownloadEndpoints:
    """Tests for download endpoints."""
    
    def test_download_nonexistent_caption(self, client, api_key):
        """Test downloading non-existent caption file."""
        response = client.get(
            "/api/v1/captions/download/nonexistent.mp4",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 404
    
    def test_download_nonexistent_frames(self, client, api_key):
        """Test downloading non-existent frames file."""
        response = client.get(
            "/api/v1/frames/download/nonexistent.zip",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 404
