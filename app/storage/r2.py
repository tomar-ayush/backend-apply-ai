import io
from app.common.logging import get_logger
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.common.exceptions import ExternalServiceError

logger = get_logger(__name__)


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
            logger.error("r2_upload_error key=%s error=%s", key, str(e))
            raise ExternalServiceError("R2", str(e))

    def upload_text(self, key: str, text: str, content_type: str = "text/plain") -> str:
        return self.upload_bytes(key, text.encode("utf-8"), content_type)

    def download_bytes(self, key: str) -> bytes:
        try:
            client = self._get_client()
            response = client.get_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as e:
            logger.error("r2_download_error key=%s error=%s", key, str(e))
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
            logger.error("r2_delete_error key=%s error=%s", key, str(e))

    def generate_presigned_put_url(
        self, key: str, content_type: str = "text/x-tex", expires_in: int = 900
    ) -> str:
        """Return a presigned PUT URL the client can use to upload a file directly to R2."""
        try:
            client = self._get_client()
            return client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": settings.R2_BUCKET_NAME,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as e:
            logger.error("r2_presign_put_error key=%s error=%s", key, str(e))
            raise ExternalServiceError("R2", str(e))

    def generate_presigned_get_url(self, key: str, expires_in: int = 900) -> str:
        """Return a presigned GET URL the client can use to download a file from R2."""
        try:
            client = self._get_client()
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as e:
            logger.error("r2_presign_get_error key=%s error=%s", key, str(e))
            raise ExternalServiceError("R2", str(e))


r2_storage = R2Storage()
