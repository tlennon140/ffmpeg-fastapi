# Testing Plan

## Goals
- Validate API correctness for all routes, especially file handling and ffmpeg workflows.
- Prevent regressions in upload limits, auth, and rate limiting.
- Cover storage integration (R2) with both configured and unconfigured states.

## Test Types

### 1) Unit Tests
- Config parsing
  - `settings.api_keys_list`, `cors_origins_list`, `allowed_*_extensions_list`, `max_upload_size_bytes`.
- Utilities
  - `validate_file_extension` rejects bad extensions.
  - `save_upload_file` enforces size limit and cleans up partial writes.
  - `generate_output_path`/`generate_temp_path` uniqueness.
- FFmpeg helpers
  - `_parse_aspect_ratio` rejects invalid ratios.
  - `_format_ass_time` and `_format_srt_time` formatting.
  - `_ass_color_with_alpha` and `_escape_drawtext_text` edge cases.
- R2 service
  - `_build_object_key` prefixing rules.
  - `build_public_url` and `generate_presigned_url` error handling (mocked boto3).

### 2) API Route Tests (FastAPI TestClient)
- Auth
  - Missing/invalid API key returns 401/403.
- Captions
  - Invalid captions JSON returns 400.
  - Image captioning success (mock ffmpeg and R2).
- Frames
  - Invalid FPS returns 422.
  - Frame extraction and ZIP creation (mock ffmpeg).
- Videos
  - Concat invalid URL returns 422.
  - Audio replace/mix success (mock ffmpeg).
  - Aspect ratio invalid value returns 400.
  - Watermark invalid position returns 400.
- Storage
  - Upload and presigned URL success (mock R2 client).
  - Output upload missing returns 404.

### 3) Integration Tests (Local ffmpeg installed)
- Smoke test each endpoint with real media inputs:
  - Short MP4 and PNG fixtures (keep small to avoid large test runtimes).
  - Validate output files are created and readable with ffprobe.
- Verify multi-step flows:
  - Upload -> process -> download -> file integrity check.

### 4) Security/Abuse Tests
- Path traversal on download endpoints using filenames like `../...` should be rejected.
- Upload extension restrictions enforced for all upload endpoints.
- Rate limiting behavior:
  - Exempt endpoints are not rate-limited.
  - Non-exempt endpoints return 429 after threshold.

### 5) Operational Tests
- Startup without ffmpeg should fail fast with clear error.
- R2 misconfiguration returns 500 with actionable message.
- Temp/output cleanup respects configured directories.

## Test Data
- Use minimal fixture files in tests (or generate in-memory bytes).
- Keep mp4 fixtures under a few seconds to minimize ffmpeg runtime.
- Store fixtures in `tests/fixtures/` if needed.

## Suggested Commands
- Unit/API tests: `pytest -q`
- Integration tests (ffmpeg required): `pytest -q -m integration`

## CI Recommendations
- Run unit/API tests on every PR.
- Run integration tests on a scheduled job or when ffmpeg is available in CI.
- Fail builds on coverage regression if coverage gates are added.
