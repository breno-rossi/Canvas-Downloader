import logging
from pathlib import Path

import requests

log = logging.getLogger("canvas")

_CHUNK = 524288  # 512 KB


class Downloader:
    def __init__(self, chunk_size: int = _CHUNK):
        self.chunk_size = chunk_size

    def download_file(self, url: str, dest_path: Path, expected_size: int | None = None) -> str:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        if dest_path.exists():
            current = dest_path.stat().st_size
            if expected_size is not None and current == expected_size:
                log.debug("Pulando (completo): %s", dest_path.name)
                return "skipped"
            if expected_size is not None and current < expected_size:
                # resume download
                headers = {"Range": f"bytes={current}-"}
                mode = "ab"
                log.debug("Retomando download de %s (offset %d)", dest_path.name, current)
            else:
                # size mismatch or unknown — re-download
                headers = {}
                mode = "wb"
        else:
            headers = {}
            mode = "wb"

        # Use bare requests.get (not authenticated session) — Canvas redirects to S3,
        # and S3 rejects requests that contain an Authorization header alongside its
        # own pre-signed query parameters.
        try:
            resp = requests.get(url, headers=headers, stream=True,
                                timeout=60, allow_redirects=True)
        except requests.RequestException as exc:
            raise RuntimeError(f"Erro de rede: {exc}") from exc

        if resp.status_code == 416:
            log.debug("Pulando (Range 416 — já completo): %s", dest_path.name)
            return "skipped"

        resp.raise_for_status()

        with open(dest_path, mode) as fh:
            for chunk in resp.iter_content(self.chunk_size):
                if chunk:
                    fh.write(chunk)

        return "downloaded"

    def save_html(self, content: str, dest_path: Path) -> str:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(content, encoding="utf-8")
        return "downloaded"
