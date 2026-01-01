# FFMPEG Media Processing API

A production-ready FastAPI service for video and image processing using FFMPEG. Deploy anywhere with Docker, including Coolify.

## Features

- **Video Captions**: Add timed subtitles/captions to videos
- **Image Captions**: Add text overlays to images
- **Frame Extraction**: Extract frames at regular intervals (1/sec, 2/sec, etc.)
- **Last Frame**: Extract the final frame from any video
- **Rate Limiting**: Built-in request throttling per API key
- **API Key Authentication**: Secure endpoints with configurable API keys
- **Health Checks**: Ready for orchestration platforms

## Quick Start

### Local Development

```bash
# Clone the repository
git clone <your-repo-url>
cd ffmpeg-api

# Copy environment file
cp .env.example .env
# Edit .env with your API keys

# Start with Docker Compose
docker-compose up --build
```

The API will be available at `http://localhost:8000`

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health Check: `http://localhost:8000/health`

### Without Docker (Development)

```bash
# Install FFMPEG
# macOS: brew install ffmpeg
# Ubuntu: apt-get install ffmpeg
# Windows: choco install ffmpeg

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## API Endpoints

### Health

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | No | Basic health check |
| `/health/ready` | GET | No | Readiness check for orchestration |

### Captions

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/captions/video` | POST | Yes | Add captions to video |
| `/api/v1/captions/image` | POST | Yes | Add text overlay to image |
| `/api/v1/captions/download/{filename}` | GET | Yes | Download processed file |

### Frames

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/frames/extract` | POST | Yes | Extract frames at interval |
| `/api/v1/frames/last` | POST | Yes | Extract last frame |
| `/api/v1/frames/download/{filename}` | GET | Yes | Download extracted frames |

## Usage Examples

### Add Captions to Video

```bash
curl -X POST "http://localhost:8000/api/v1/captions/video" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F 'captions_json=[{"text":"Hello World","start":0,"end":3},{"text":"Goodbye","start":4,"end":6}]' \
  -F "font_size=32" \
  -F "position=bottom"
```

### Add Caption to Image

```bash
curl -X POST "http://localhost:8000/api/v1/captions/image" \
  -H "X-API-Key: your-api-key" \
  -F "image=@image.jpg" \
  -F "text=Hello World" \
  -F "font_size=48" \
  -F "position=center"
```

### Extract Frames (2 per second)

```bash
curl -X POST "http://localhost:8000/api/v1/frames/extract" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "fps=2" \
  -F "format=jpg"
```

### Extract Last Frame

```bash
curl -X POST "http://localhost:8000/api/v1/frames/last" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "format=png"
```

### Download Result

```bash
curl -X GET "http://localhost:8000/api/v1/captions/download/captioned_abc123.mp4" \
  -H "X-API-Key: your-api-key" \
  -o output.mp4
```

## Configuration

All settings can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEYS` | `dev-key-change-me` | Comma-separated API keys |
| `RATE_LIMIT_REQUESTS` | `100` | Requests per window |
| `RATE_LIMIT_WINDOW` | `60` | Rate limit window (seconds) |
| `RATE_LIMIT_UPLOAD_REQUESTS` | `10` | Upload requests per window |
| `MAX_UPLOAD_SIZE_MB` | `500` | Max upload size |
| `FFMPEG_TIMEOUT` | `300` | Operation timeout (seconds) |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `ENABLE_DOCS` | `true` | Enable Swagger/ReDoc |

## Deployment to Coolify

### Option 1: Git Repository

1. Push this code to a Git repository (GitHub, GitLab, etc.)

2. In Coolify:
   - Create a new Resource → Application
   - Select your Git provider and repository
   - Choose **Dockerfile** as the build pack
   - Set the following environment variables:
     ```
     API_KEYS=your-secure-production-key-1,your-secure-production-key-2
     CORS_ORIGINS=https://yourdomain.com
     ENABLE_DOCS=false
     ```

3. Configure the following in Coolify:
   - **Port**: 8000
   - **Health Check Path**: `/health`
   - **Health Check Interval**: 30s

### Option 2: Docker Image

1. Build and push to a registry:
   ```bash
   docker build -t your-registry/ffmpeg-api:latest .
   docker push your-registry/ffmpeg-api:latest
   ```

2. In Coolify:
   - Create a new Resource → Application
   - Select **Docker Image**
   - Enter your image: `your-registry/ffmpeg-api:latest`
   - Configure environment variables as above

### Coolify Environment Variables

Set these in Coolify's Environment Variables section:

```env
# Required - Change these!
API_KEYS=generate-secure-random-keys-here

# Recommended for production
CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
ENABLE_DOCS=false
RATE_LIMIT_REQUESTS=50
MAX_UPLOAD_SIZE_MB=100
FILE_RETENTION_SECONDS=1800
```

### Resource Recommendations

For Coolify deployment:

- **Memory**: 1-2 GB minimum (more for large video processing)
- **CPU**: 1-2 cores minimum
- **Storage**: Depends on video sizes; output files are temporary

## Security Considerations

1. **API Keys**: Generate strong, random API keys for production:
   ```bash
   openssl rand -hex 32
   ```

2. **CORS**: Restrict to your specific domains in production

3. **Rate Limiting**: Adjust limits based on your use case

4. **File Cleanup**: Output files are automatically cleaned up after `FILE_RETENTION_SECONDS`

5. **Documentation**: Consider disabling `/docs` and `/redoc` in production

## Rate Limiting

The API implements per-key rate limiting:

- **General endpoints**: 100 requests/minute (configurable)
- **Upload endpoints**: 10 requests/minute (configurable)

Rate limit headers are included in responses:
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining
- `X-RateLimit-Reset`: Seconds until reset

## Supported Formats

### Video Input
- MP4, AVI, MOV, MKV, WebM, FLV, WMV

### Image Input
- JPG, JPEG, PNG, GIF, BMP, WebP, TIFF

### Output
- Videos: Same format as input
- Images: Same format as input, or JPG/PNG for frames

## Error Handling

The API returns standard HTTP status codes:

- `200`: Success
- `400`: Bad request (invalid input)
- `401`: Unauthorized (missing API key)
- `403`: Forbidden (invalid API key)
- `404`: Not found
- `413`: File too large
- `429`: Rate limit exceeded
- `500`: Server error

Error responses include a `detail` field with a description.

## License

MIT License - See LICENSE file for details.
