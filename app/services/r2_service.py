"""
Cloudflare R2 storage helpers.
"""

import asyncio
from dataclasses import dataclass
import mimetypes
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.config import settings


@dataclass
class R2UploadResult:
    """Result of an R2 upload."""
    key: str
    url: str


class R2Service:
    """Service for uploading files to Cloudflare R2."""
    
    def _require_config(self) -> None:
        missing = []
        if not settings.R2_ACCESS_KEY_ID:
            missing.append("R2_ACCESS_KEY_ID")
        if not settings.R2_SECRET_ACCESS_KEY:
            missing.append("R2_SECRET_ACCESS_KEY")
        if not settings.R2_BUCKET:
            missing.append("R2_BUCKET")
        if not settings.R2_ACCOUNT_ID and not settings.R2_ENDPOINT_URL:
            missing.append("R2_ACCOUNT_ID or R2_ENDPOINT_URL")
        
        if missing:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"R2 is not configured (missing: {', '.join(missing)})"
            )
    
    def _endpoint_url(self) -> str:
        if settings.R2_ENDPOINT_URL:
            return settings.R2_ENDPOINT_URL
        return f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    
    def _client(self):
        self._require_config()
        return boto3.client(
            "s3",
            endpoint_url=self._endpoint_url(),
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name=settings.R2_REGION,
        )
    
    def _build_object_key(self, filename: str, key_prefix: str) -> str:
        ext = ""
        if "." in filename:
            ext = "." + filename.split(".")[-1].lower()
        if not ext:
            ext = ".bin"
        
        prefix_parts = [
            settings.R2_KEY_PREFIX.strip("/"),
            key_prefix.strip("/"),
        ]
        prefix = "/".join(part for part in prefix_parts if part)
        if prefix:
            prefix = f"{prefix}/"
        
        return f"{prefix}{uuid.uuid4().hex}{ext}"
    
    def build_public_url(self, key: str) -> str:
        base_url = settings.R2_PUBLIC_BASE_URL
        if not base_url:
            if not settings.R2_ACCOUNT_ID:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="R2_PUBLIC_BASE_URL is required when R2_ACCOUNT_ID is not set"
                )
            base_url = f"https://{settings.R2_BUCKET}.{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        return f"{base_url.rstrip('/')}/{key.lstrip('/')}"
    
    async def upload_file_path(
        self,
        file_path: str,
        filename: str,
        key_prefix: str = ""
    ) -> R2UploadResult:
        """
        Upload a local file to R2.
        
        Args:
            file_path: Local file path
            filename: Original filename (for extension/mime)
            key_prefix: Optional key prefix
            
        Returns:
            R2UploadResult
        """
        client = self._client()
        key = self._build_object_key(filename, key_prefix)
        content_type, _ = mimetypes.guess_type(filename)
        extra_args = {"ContentType": content_type} if content_type else None
        
        try:
            if extra_args:
                await asyncio.to_thread(
                    client.upload_file,
                    file_path,
                    settings.R2_BUCKET,
                    key,
                    ExtraArgs=extra_args,
                )
            else:
                await asyncio.to_thread(
                    client.upload_file,
                    file_path,
                    settings.R2_BUCKET,
                    key,
                )
        except (BotoCoreError, ClientError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to upload to R2: {exc}"
            ) from exc
        
        return R2UploadResult(key=key, url=self.build_public_url(key))


r2_service = R2Service()
