"""
Storage endpoints for R2 uploads.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.services.r2_service import r2_service
from app.utils.auth import verify_api_key
from app.utils.files import cleanup_file, save_upload_file

router = APIRouter()


class R2UploadResponse(BaseModel):
    """Response model for R2 uploads."""
    success: bool
    key: str
    url: str
    message: str


@router.post(
    "/storage/r2/upload",
    response_model=R2UploadResponse,
    summary="Upload file to R2",
    description="Upload a file to Cloudflare R2 and return a public URL."
)
async def upload_to_r2(
    file: UploadFile = File(..., description="File to upload"),
    key_prefix: str = Form(default="", description="Optional key prefix"),
    api_key: str = Depends(verify_api_key)
):
    """
    Upload an arbitrary file to R2.
    """
    input_path = None
    try:
        input_path, _ = await save_upload_file(
            file,
            settings.r2_allowed_extensions_list,
            prefix="r2_upload_"
        )
        
        result = await r2_service.upload_file_path(
            file_path=input_path,
            filename=file.filename or "upload.bin",
            key_prefix=key_prefix
        )
        
        return R2UploadResponse(
            success=True,
            key=result.key,
            url=result.url,
            message="File uploaded to R2 successfully"
        )
        
    finally:
        if input_path:
            cleanup_file(input_path)


@router.post(
    "/storage/r2/upload/output/{filename}",
    response_model=R2UploadResponse,
    summary="Upload output file to R2",
    description="Upload a processed output file to Cloudflare R2 and return a public URL."
)
async def upload_output_to_r2(
    filename: str,
    key_prefix: Optional[str] = Query(default=""),
    api_key: str = Depends(verify_api_key)
):
    """
    Upload an existing output file to R2 by filename.
    """
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename"
        )
    
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in settings.r2_allowed_extensions_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' not allowed"
        )
    
    file_path = os.path.join(settings.OUTPUT_DIR, safe_name)
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or expired"
        )
    
    result = await r2_service.upload_file_path(
        file_path=file_path,
        filename=safe_name,
        key_prefix=key_prefix or ""
    )
    
    return R2UploadResponse(
        success=True,
        key=result.key,
        url=result.url,
        message="Output file uploaded to R2 successfully"
    )
