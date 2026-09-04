from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx

from dacpactools.presentation.logging_config import get_logger

log = get_logger("nuget_fetcher")


class NugetError(Exception):
    pass


class NugetFetcher:
    """Fetches and caches .nupkg contents from a v3 flat-container feed."""

    def __init__(self, source: str, cache_dir: Path, timeout: float = 60.0) -> None:
        self.source = source.rstrip("/")
        self.cache_dir = cache_dir
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NugetFetcher":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def resolve_latest_stable(self, package_id: str) -> str:
        pid = package_id.lower()
        url = f"{self.source}/{pid}/index.json"
        log.debug("nuget: resolve latest", package=package_id, url=url)
        r = self._client.get(url)
        if r.status_code != 200:
            raise NugetError(f"failed to list versions for {package_id}: {r.status_code} {url}")
        data = json.loads(r.text)
        versions = [v for v in data.get("versions", []) if "-" not in v]
        if not versions:
            raise NugetError(f"no stable versions found for {package_id}")
        return versions[-1]

    def ensure(self, package_id: str, version: str | None = None) -> Path:
        pid = package_id.lower()
        resolved = version or self.resolve_latest_stable(package_id)
        target = self.cache_dir / pid / resolved
        marker = target / ".extracted"
        if marker.exists():
            log.debug("nuget: cache hit", package=package_id, version=resolved, path=str(target))
            return target
        target.mkdir(parents=True, exist_ok=True)
        url = f"{self.source}/{pid}/{resolved}/{pid}.{resolved}.nupkg"
        log.info("nuget: downloading", package=package_id, version=resolved, url=url)
        r = self._client.get(url)
        if r.status_code != 200:
            raise NugetError(f"failed to download {url}: {r.status_code}")
        nupkg_path = target / f"{pid}.{resolved}.nupkg"
        nupkg_path.write_bytes(r.content)
        with zipfile.ZipFile(nupkg_path) as zf:
            for name in zf.namelist():
                # skip signing artefacts and _rels
                if name.startswith("_rels/") or name.startswith("package/") or name.startswith("[Content_Types].xml") or name == ".signature.p7s":
                    continue
                dest = target / name
                if name.endswith("/"):
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(name))
        marker.write_text(resolved, encoding="utf-8")
        log.info("nuget: extracted", package=package_id, version=resolved, path=str(target))
        return target
