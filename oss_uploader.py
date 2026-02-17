from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import oss2
from dotenv import load_dotenv
from PIL import Image


class OSSUploader:
    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str,
        bucket_name: str,
        domain: str,
    ) -> None:
        self._domain = domain.rstrip("/")
        auth = oss2.Auth(access_key_id, access_key_secret)
        self._bucket = oss2.Bucket(auth, endpoint, bucket_name)

    @classmethod
    def from_env(cls) -> "OSSUploader":
        env_path = Path(__file__).resolve().parent / ".env"
        load_dotenv(dotenv_path=env_path, override=False)
        required = {
            "OSS_ACCESS_KEY_ID": os.getenv("OSS_ACCESS_KEY_ID", "").strip(),
            "OSS_ACCESS_KEY_SECRET": os.getenv("OSS_ACCESS_KEY_SECRET", "").strip(),
            "OSS_ENDPOINT": os.getenv("OSS_ENDPOINT", "").strip(),
            "OSS_BUCKET_NAME": os.getenv("OSS_BUCKET_NAME", "").strip(),
            "OSS_DOMAIN": os.getenv("OSS_DOMAIN", "").strip(),
        }

        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                "Missing OSS configuration in .env: " + ", ".join(sorted(missing))
            )

        return cls(
            access_key_id=required["OSS_ACCESS_KEY_ID"],
            access_key_secret=required["OSS_ACCESS_KEY_SECRET"],
            endpoint=required["OSS_ENDPOINT"],
            bucket_name=required["OSS_BUCKET_NAME"],
            domain=required["OSS_DOMAIN"],
        )

    def upload_file(self, local_file_path: Path, oss_file_path: str) -> str:
        object_key = oss_file_path.lstrip("/")

        content_type, _ = mimetypes.guess_type(local_file_path.name)
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type

        self._bucket.put_object_from_file(object_key, str(local_file_path), headers=headers)
        return f"{self._domain}/{object_key}"


def preprocess_image_for_wechat(
    local_file_path: Path,
    output_path: Path,
    max_width: int = 1080,
    max_size_mb: int = 2,
    output_format: str = "WEBP",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(local_file_path) as image:
        if image.width > max_width:
            new_height = int(image.height * (max_width / image.width))
            image = image.resize((max_width, new_height), Image.Resampling.LANCZOS)

        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        quality = 90
        while True:
            image.save(output_path, format=output_format, quality=quality)
            size_mb = output_path.stat().st_size / 1024 / 1024
            if size_mb <= max_size_mb or quality <= 30:
                break
            quality -= 10

    return output_path
