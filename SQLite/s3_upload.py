from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import boto3

@dataclass
class UploadResult:
    bucket: str
    key: str
    url: str

def upload_file_to_s3(file_path: str | Path, key: str, bucket: str = "ansonsupermart.com", region: str = "ap-southeast-1", content_type: str = "image/jpeg", public_read: bool = True) -> UploadResult:
    file_path = Path(file_path)
    s3 = boto3.client("s3", region_name=region)
    extra = {"ContentType": content_type}
    s3.upload_file(str(file_path), bucket, key, ExtraArgs=extra)
    url = f"https://s3-{region}.amazonaws.com/{bucket}/{key}"
    return UploadResult(bucket=bucket, key=key, url=url)
