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

### Videos

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/videos/concat` | POST | Yes | Concatenate video segments from URLs |
| `/api/v1/videos/audio` | POST | Yes | Add or replace audio on a video |
| `/api/v1/videos/aspect` | POST | Yes | Convert video aspect ratio (pad) |
| `/api/v1/videos/crop/vertical` | POST | Yes | Smart crop for vertical video |
| `/api/v1/videos/watermark` | POST | Yes | Add watermark/logo overlay |
| `/api/v1/videos/append` | POST | Yes | Append intro/outro to video |
| `/api/v1/videos/audio/extract` | POST | Yes | Extract audio from video |

### Storage

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/storage/r2/upload` | POST | Yes | Upload file to R2 |
| `/api/v1/storage/r2/upload/output/{filename}` | POST | Yes | Upload output file to R2 |

## Usage Examples

### Add Captions to Video

```bash
curl -X POST "http://localhost:8000/api/v1/captions/video" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F 'captions_json=[{"text":"Hello World","start":0,"end":3},{"text":"Goodbye","start":4,"end":6}]' \
  -F "position=bottom"
```

### Add Caption to Image

```bash
curl -X POST "http://localhost:8000/api/v1/captions/image" \
  -H "X-API-Key: your-api-key" \
  -F "image=@image.jpg" \
  -F "text=Hello World" \
  -F "position=center"
```

### Concatenate Videos

```bash
curl -X POST "http://localhost:8000/api/v1/videos/concat" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "segments": [
      {"url": "https://example.com/video1.mp4", "start": 0, "end": 4.5},
      {"url": "https://example.com/video2.mp4", "start": 2, "end": 8}
    ]
  }'
```

### Upload to R2

```bash
curl -X POST "http://localhost:8000/api/v1/storage/r2/upload" \
  -H "X-API-Key: your-api-key" \
  -F "file=@video.mp4" \
  -F "key_prefix=uploads"
```

### Upload Output to R2

```bash
curl -X POST "http://localhost:8000/api/v1/storage/r2/upload/output/captioned_abc123.mp4?key_prefix=outputs" \
  -H "X-API-Key: your-api-key"
```

### Add or Replace Audio

```bash
curl -X POST "http://localhost:8000/api/v1/videos/audio" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "audio=@track.mp3" \
  -F "replace_audio=true"
```

### Add Watermark

```bash
curl -X POST "http://localhost:8000/api/v1/videos/watermark" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "logo=@logo.png" \
  -F "position=top-right" \
  -F "opacity=0.9"
```

### Append Intro/Outro

```bash
curl -X POST "http://localhost:8000/api/v1/videos/append" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "intro=@intro.mp4" \
  -F "outro=@outro.mp4"
```

### Extract Audio

```bash
curl -X POST "http://localhost:8000/api/v1/videos/audio/extract" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "format=mp3"
```

### Convert Aspect Ratio

```bash
curl -X POST "http://localhost:8000/api/v1/videos/aspect" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "ratio=9:16"
```

### Smart Crop for Vertical

```bash
curl -X POST "http://localhost:8000/api/v1/videos/crop/vertical" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F "ratio=9:16"
```
### Auto-Upload Results to R2

Processing endpoints accept these optional parameters:
- `upload` (boolean): Upload the generated output to R2.
- `upload_location`: Optional key prefix within the bucket.
Responses include `r2_key` and `r2_url` when `upload=true`.

Example (video captions):

```bash
curl -X POST "http://localhost:8000/api/v1/captions/video" \
  -H "X-API-Key: your-api-key" \
  -F "video=@video.mp4" \
  -F 'captions_json=[{"text":"Hello","start":0,"end":2}]' \
  -F "upload=true" \
  -F "upload_location=captions"
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

## Testing

Run the test suite:

```bash
pytest
```

Notes:
- Route tests cover all endpoints and use mocks to avoid calling FFMPEG or external URLs.
- Tests isolate input/output into temporary directories.
- R2 tests mock uploads; no network access required.

## Configuration

All settings can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEYS` | `dev-key-change-me` | Comma-separated API keys |
| `RATE_LIMIT_REQUESTS` | `100` | Requests per window |
| `RATE_LIMIT_WINDOW` | `60` | Rate limit window (seconds) |
| `RATE_LIMIT_UPLOAD_REQUESTS` | `10` | Upload requests per window |
| `MAX_UPLOAD_SIZE_MB` | `500` | Max upload size |
| `ALLOWED_VIDEO_EXTENSIONS` | `.mp4,.avi,.mov,.mkv,.webm,.flv,.wmv` | Allowed video extensions |
| `ALLOWED_IMAGE_EXTENSIONS` | `.jpg,.jpeg,.png,.gif,.bmp,.webp,.tiff` | Allowed image extensions |
| `ALLOWED_AUDIO_EXTENSIONS` | `.mp3,.wav,.aac,.m4a,.ogg,.flac` | Allowed audio extensions |
| `FFMPEG_TIMEOUT` | `300` | Operation timeout (seconds) |
| `CORS_ORIGINS` | `*` | Allowed CORS origins |
| `ENABLE_DOCS` | `true` | Enable Swagger/ReDoc |
| `R2_ACCOUNT_ID` | `None` | Cloudflare R2 account ID |
| `R2_ACCESS_KEY_ID` | `None` | Cloudflare R2 access key ID |
| `R2_SECRET_ACCESS_KEY` | `None` | Cloudflare R2 secret access key |
| `R2_BUCKET` | `None` | Cloudflare R2 bucket name |
| `R2_REGION` | `auto` | Cloudflare R2 region |
| `R2_ENDPOINT_URL` | `None` | Optional custom R2 endpoint URL |
| `R2_PUBLIC_BASE_URL` | `None` | Public base URL for R2 objects |
| `R2_KEY_PREFIX` | `` | Optional prefix for R2 object keys |
| `R2_ALLOWED_EXTENSIONS` | `.mp4,...,.zip` | Allowed extensions for R2 uploads |
| `CAPTION_FONT` | `Arial` | Caption font name (Arial, Raleway, Montserrat, Roboto) |
| `CAPTION_FONT_FOLDER` | `fonts` | Folder containing caption font files |

### R2 Storage Notes

- Set `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET` before using storage endpoints.
- Set `R2_PUBLIC_BASE_URL` to your public bucket domain or custom CDN domain.
- If `R2_PUBLIC_BASE_URL` is not set, URLs default to `https://{bucket}.{account_id}.r2.cloudflarestorage.com/{key}`.

### R2 Environment Variables

Use these in `.env` or your deployment environment:

```env
# Required
R2_ACCOUNT_ID=your-account-id
R2_ACCESS_KEY_ID=your-access-key
R2_SECRET_ACCESS_KEY=your-secret-key
R2_BUCKET=your-bucket

# Recommended for public URLs
R2_PUBLIC_BASE_URL=https://your-public-domain

# Optional
R2_REGION=auto
R2_ENDPOINT_URL=
R2_KEY_PREFIX=
R2_ALLOWED_EXTENSIONS=.mp4,.avi,.mov,.mkv,.webm,.flv,.wmv,.jpg,.jpeg,.png,.gif,.bmp,.webp,.tiff,.zip
```

## Caption Styling Defaults

By default, captions use a TikTok-style look:

- Arial font (via `CAPTION_FONT` and `CAPTION_FONT_FOLDER`)
- Auto-sized text (about 1.4% of media height when `font_size` is omitted)
- Max width ~72% of video width, wrapped to 2 lines
- Line height ~1.2x
- Safe margins: ~6% left/right, ~12% bottom
- Semi-transparent background box (`black@0.65`)

Available bundled fonts in `fonts/`: Arial, Raleway, Montserrat, Roboto.

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
