"""S3-совместимые бакеты: операционные файлы и архив новостей.

Секреты только из окружения (Vault), в репозиторий не попадают.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BucketConfig:
    name: str
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class ObjectStorageSettings:
    endpoint: str
    region: str
    files: BucketConfig | None
    archive: BucketConfig | None


def _bucket(name_key: str, access_key: str, secret_key: str) -> BucketConfig | None:
    name = os.getenv(name_key, "").strip()
    access = os.getenv(access_key, "").strip()
    secret = os.getenv(secret_key, "").strip()
    if not (name and access and secret):
        return None
    return BucketConfig(name=name, access_key=access, secret_key=secret)


def load_storage_settings() -> ObjectStorageSettings:
    return ObjectStorageSettings(
        endpoint=os.getenv("S3_ENDPOINT", "").strip().rstrip("/"),
        region=os.getenv("S3_REGION", "us-east-1").strip() or "us-east-1",
        files=_bucket("S3_FILES_BUCKET", "S3_FILES_ACCESS_KEY", "S3_FILES_SECRET_KEY"),
        archive=_bucket(
            "S3_ARCHIVE_BUCKET", "S3_ARCHIVE_ACCESS_KEY", "S3_ARCHIVE_SECRET_KEY"
        ),
    )


def _client(settings: ObjectStorageSettings, bucket: BucketConfig):
    if not settings.endpoint:
        raise RuntimeError("S3_ENDPOINT не задан.")
    try:
        import boto3
        from botocore.client import Config
    except ImportError as exc:
        raise RuntimeError("Установите boto3 (requirements-navigator.txt).") from exc
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        region_name=settings.region,
        aws_access_key_id=bucket.access_key,
        aws_secret_access_key=bucket.secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": os.getenv("S3_ADDRESSING_STYLE", "path")},
        ),
    )


def put_bytes(bucket: BucketConfig, key: str, body: bytes, content_type: str) -> str:
    settings = load_storage_settings()
    client = _client(settings, bucket)
    client.put_object(
        Bucket=bucket.name,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    return f"s3://{bucket.name}/{key}"


def object_exists(bucket: BucketConfig, key: str) -> bool:
    settings = load_storage_settings()
    client = _client(settings, bucket)
    try:
        client.head_object(Bucket=bucket.name, Key=key)
    except Exception as exc:
        response = getattr(exc, "response", None) or {}
        code = response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    return True
