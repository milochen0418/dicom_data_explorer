import reflex as rx
from datetime import datetime
import asyncio
import logging
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from reflex_ddns_auth.collections import Collection


def _download_stream(url: str, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=(10, 120)) as response:
        response.raise_for_status()
        with open(dest_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def _normalize_s3_prefix(prefix: str) -> str:
    cleaned = prefix.replace("*", "")
    return cleaned


def _parse_s3_url(series_aws_url: str) -> tuple[str, str]:
    if not series_aws_url:
        raise ValueError("Missing series_aws_url for IDC download")
    parsed = urlparse(series_aws_url)
    if parsed.scheme == "s3":
        bucket = parsed.netloc
        prefix = _normalize_s3_prefix(parsed.path.lstrip("/"))
        return bucket, prefix
    if parsed.scheme in {"http", "https"}:
        host = parsed.netloc
        if host.endswith(".s3.amazonaws.com"):
            bucket = host.split(".s3.amazonaws.com")[0]
            prefix = _normalize_s3_prefix(parsed.path.lstrip("/"))
            return bucket, prefix
        if host == "s3.amazonaws.com":
            path_parts = parsed.path.lstrip("/").split("/", 1)
            bucket = path_parts[0]
            prefix = _normalize_s3_prefix(path_parts[1] if len(path_parts) > 1 else "")
            return bucket, prefix
    raise ValueError(f"Unsupported series_aws_url format: {series_aws_url}")


def _list_s3_objects(bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    continuation: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if continuation:
            params["continuation-token"] = continuation
        response = requests.get(
            f"https://{bucket}.s3.amazonaws.com",
            params=params,
            timeout=(10, 120),
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        for key_node in root.findall(".//{*}Contents/{*}Key"):
            if key_node.text:
                keys.append(key_node.text)
        token_node = root.find(".//{*}NextContinuationToken")
        if token_node is None or not token_node.text:
            break
        continuation = token_node.text
    return keys


def _get_idc_keys(series_aws_url: str) -> tuple[str, str, list[str]]:
    bucket, prefix = _parse_s3_url(series_aws_url)
    keys = [key for key in _list_s3_objects(bucket, prefix) if not key.endswith("/")]
    if not keys:
        raise ValueError(f"No objects found for IDC series at {series_aws_url}")
    return bucket, prefix, keys


class DownloadState(rx.State):
    cart_items: list[dict] = []
    download_history: list[dict] = []
    is_downloading: bool = False
    download_progress: int = 0
    total_files: int = 0
    downloaded_files: int = 0
    current_series_uid: str = ""
    progress_message: str = ""

    @rx.var
    def cart_count(self) -> int:
        return len(self.cart_items)

    @rx.var
    def total_size_mb(self) -> float:
        total = 0.0
        for item in self.cart_items:
            size = item.get("series_size_MB")
            if size:
                try:
                    total += float(size)
                except (ValueError, TypeError) as e:
                    logging.exception(f"Error calculating size for item: {e}")
        return round(total, 2)

    @rx.event
    def add_to_cart(self, series: dict, source: str):
        for item in self.cart_items:
            if item["SeriesInstanceUID"] == series["SeriesInstanceUID"]:
                return
        item = series.copy()
        item["source"] = source
        item["added_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cart_items.append(item)

    @rx.event
    def remove_from_cart(self, series_uid: str):
        self.cart_items = [
            item for item in self.cart_items if item["SeriesInstanceUID"] != series_uid
        ]

    @rx.event
    def clear_cart(self):
        self.cart_items = []

    @rx.event
    async def start_download(self):
        """Download selected series and store DICOM files locally."""
        if not self.cart_items:
            return
        self.is_downloading = True
        self.download_progress = 0
        self.total_files = 0
        self.downloaded_files = 0
        self.current_series_uid = ""
        self.progress_message = "Preparing download..."
        idc_plan: dict[str, dict] = {}
        yield
        for item in self.cart_items:
            source = item.get("source", "").upper()
            if source == "IDC":
                series_aws_url = item.get("series_aws_url", "")
                try:
                    bucket, prefix, keys = await asyncio.to_thread(
                        _get_idc_keys, series_aws_url
                    )
                    idc_plan[item.get("SeriesInstanceUID", "")] = {
                        "bucket": bucket,
                        "prefix": prefix,
                        "keys": keys,
                    }
                    self.total_files += len(keys)
                except Exception as e:
                    logging.exception("IDC listing failed for %s: %s", item, e)
            else:
                logging.warning("Unsupported source in cart: %s", source)
            yield
        if self.total_files == 0:
            self.total_files = len(self.cart_items)
        self.progress_message = "Downloading..."
        yield
        completed_items: list[dict] = []
        # Each series becomes one item of the shared "dicom" collection.
        dicom = Collection("dicom")
        for item in self.cart_items:
            try:
                source = item.get("source", "").upper()
                self.current_series_uid = item.get("SeriesInstanceUID", "")
                if source != "IDC":
                    logging.warning("Skipping unsupported source: %s", source)
                    yield
                    continue
                series_aws_url = item.get("series_aws_url", "")
                plan = idc_plan.get(self.current_series_uid)
                if not plan:
                    bucket, prefix, keys = await asyncio.to_thread(
                        _get_idc_keys, series_aws_url
                    )
                    plan = {"bucket": bucket, "prefix": prefix, "keys": keys}
                prefix_path = Path(plan["prefix"])
                if dicom.has(self.current_series_uid):
                    self.downloaded_files += len(plan["keys"])  # already in the collection
                else:
                    # Files are written in a staging folder; the series shows up
                    # for other apps only when this block completes.
                    with dicom.publish(
                        self.current_series_uid,
                        kind="dicom.series",
                        title=" · ".join(str(item[k]) for k in ("Collection", "Modality") if item.get(k)),
                        source=series_aws_url,
                        meta={k: item[k] for k in ("Collection", "Modality", "BodyPartExamined", "ImageCount") if item.get(k)},
                    ) as series:
                        for key in plan["keys"]:
                            key_path = Path(key)
                            if plan["prefix"] and key.startswith(plan["prefix"]):
                                rel_path = key_path.relative_to(prefix_path)
                            else:
                                rel_path = Path(key_path.name)
                            dest_path = series.path / rel_path
                            if not dest_path.exists():
                                url = f"https://{plan['bucket']}.s3.amazonaws.com/{key}"
                                await asyncio.to_thread(_download_stream, url, dest_path)
                            self.downloaded_files += 1
                            if self.total_files > 0:
                                self.download_progress = int(
                                    self.downloaded_files / self.total_files * 100
                                )
                            yield
                history_item = item.copy()
                history_item["downloaded_at"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                history_item["ref"] = dicom.ref(self.current_series_uid)
                completed_items.append(history_item)
            except Exception as e:
                logging.exception("Download failed for %s: %s", item, e)
            yield
        for history_item in reversed(completed_items):
            self.download_history.insert(0, history_item)
        self.cart_items = []
        self.is_downloading = False
        self.download_progress = 0
        self.total_files = 0
        self.downloaded_files = 0
        self.current_series_uid = ""
        self.progress_message = ""