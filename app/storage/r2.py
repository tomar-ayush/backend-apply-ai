import io
import structlog
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.common.exceptions import ExternalServiceError

logger = structlog.get_logger()


class R2Storage:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not settings.R2_ACCOUNT_ID:
                raise ExternalServiceError("R2", "R2 credentials not configured")
            self._client = boto3.client(
                "s3",
                endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                region_name="auto",
            )
        return self._client

    def upload_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        try:
            client = self._get_client()
            client.put_object(
                Bucket=settings.R2_BUCKET_NAME,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            return self._public_url(key)
        except (BotoCoreError, ClientError) as e:
            logger.error("r2_upload_error", key=key, error=str(e))
            raise ExternalServiceError("R2", str(e))

    def upload_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        return self.upload_bytes(key, text.encode("utf-8"), content_type)

    def download_bytes(self, key: str) -> bytes:
        try:
            client = self._get_client()
            response = client.get_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as e:
            logger.error("r2_download_error", key=key, error=str(e))
            raise ExternalServiceError("R2", str(e))

    def download_text(self, key: str) -> str:
        return self.download_bytes(key).decode("utf-8")

    def key_from_url(self, url: str) -> str:
        base = settings.R2_PUBLIC_URL.rstrip("/")
        return url.replace(f"{base}/", "")

    def _public_url(self, key: str) -> str:
        base = settings.R2_PUBLIC_URL.rstrip("/")
        return f"{base}/{key}"

    def delete(self, key: str) -> None:
        try:
            client = self._get_client()
            client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
        except (BotoCoreError, ClientError) as e:
            logger.error("r2_delete_error", key=key, error=str(e))


r2_storage = R2Storage()
