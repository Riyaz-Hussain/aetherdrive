import os
import re
import fnmatch
import asyncio
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional

import gdown

MAGIC_BYTE_SIGNATURES = [
    (4, b"ftyp", ".mp4"),
    (0, b"\x1a\x45\xdf\xa3", ".mkv"),
    (0, b"RIFF", ".avi"),
    (0, b"PK\x03\x04", ".zip"),
    (0, b"Rar!\x1a\x07", ".rar"),
    (0, b"7z\xbc\xaf\x27\x1c", ".7z"),
    (0, b"%PDF", ".pdf"),
    (0, b"\x89PNG\r\n\x1a\n", ".png"),
    (0, b"\xff\xd8\xff", ".jpg"),
    (0, b"ID3", ".mp3"),
]

DEFAULT_EXCLUDE_PATTERNS = [
    "*500$*",
    "*5000$*",
    "*all in one vip*",
    "*free course*",
    "*free courses*",
    "*join telegram*",
    "*join whatsapp*",
    "*vip access*",
    "*reels bundle*",
    "*giveaway*",
    "*crack*",
    "*keygen*",
    "*content owner*",
    "*owner channel*",
    "*binus*",
]

URL_DOMAIN_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?[a-zA-Z0-9_\-]+\.(?:vercel\.app|blogspot\.com|com|net|org|io|me|xyz|top|live|site|info|online)\b",
    re.IGNORECASE,
)
SOCIAL_HANDLE_PATTERN = re.compile(r"@[a-zA-Z0-9_]+", re.IGNORECASE)
WATERMARK_WORDS = re.compile(r"\b(ahm7tech|careership|google|telegram)\b", re.IGNORECASE)


def extract_drive_folder_id(url: str) -> Optional[str]:
    patterns = [
        r"folders\/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", url.strip()):
        return url.strip()
    return None


async def parse_drive_folder(folder_url: str) -> List[Dict[str, Any]]:
    folder_id = extract_drive_folder_id(folder_url)
    if not folder_id:
        raise ValueError("Invalid Google Drive folder link or folder ID provided.")

    loop = asyncio.get_event_loop()

    def _scrape():
        items = []
        view_url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#grid"
        req = urllib.request.Request(
            view_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            matches = re.findall(
                r'data-id="([a-zA-Z0-9_-]+)".*?class="flip-entry-title"[^>]*>([^<]+)<',
                html,
                re.DOTALL
            )
            for f_id, name in matches:
                clean_name = urllib.parse.unquote(name).strip()
                if clean_name and f_id != folder_id:
                    items.append({
                        "id": f_id,
                        "name": clean_name,
                        "path": clean_name,
                        "size": 0
                    })
        except Exception:
            pass

        if not items:
            raw = gdown.download_folder(
                f"https://drive.google.com/drive/folders/{folder_id}",
                skip_download=True,
                quiet=True,
                use_cookies=False
            )
            for r in (raw or []):
                if isinstance(r, dict) and r.get("id"):
                    items.append({
                        "id": r["id"],
                        "name": r.get("name", "unnamed"),
                        "path": r.get("path", r.get("name", "unnamed")),
                        "size": r.get("size", 0)
                    })
                elif hasattr(r, "id"):
                    items.append({
                        "id": getattr(r, "id"),
                        "name": getattr(r, "name", "unnamed"),
                        "path": getattr(r, "path", getattr(r, "name", "unnamed")),
                        "size": getattr(r, "size", 0)
                    })

        unique = {}
        for item in items:
            if item["id"] not in unique:
                unique[item["id"]] = item

        return list(unique.values())

    items = await loop.run_in_executor(None, _scrape)
    if not items:
        raise ValueError("No accessible files found in this Google Drive folder. Verify it is public.")
    return items


def sanitize_path(raw_path: str) -> str:
    parts = raw_path.replace("\\", "/").strip("/").split("/")
    cleaned_parts = []

    for part in parts:
        s = URL_DOMAIN_PATTERN.sub("", part)
        s = SOCIAL_HANDLE_PATTERN.sub("", s)
        s = re.sub(r"\|.*?\|", "", s)
        s = re.sub(r"\[.*?\]", "", s)
        s = re.sub(r"\(.*?\.(?:com|org|net|app|io|xyz)\)", "", s, flags=re.IGNORECASE)
        s = WATERMARK_WORDS.sub("", s)
        s = re.sub(r'[\\/*?:"<>|]', "", s)
        s = s.encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"[_\s]+", " ", s)
        s = re.sub(r"^[-\s]+|[-\s]+$", "", s)

        if not s.strip():
            s = part.strip()
        cleaned_parts.append(s.strip())

    collapsed = []
    for part in cleaned_parts:
        if not collapsed or collapsed[-1].lower() != part.lower():
            collapsed.append(part)

    result = "/".join(collapsed)
    result = re.sub(r"(\.[a-zA-Z0-9]{2,5})\s*(\.[a-zA-Z0-9]{2,5})+$", r"\1", result)
    return result.strip()


def matches_wildcards(target: str, patterns: List[str]) -> bool:
    target_norm = target.encode("ascii", "ignore").decode("ascii").lower()
    for pattern in patterns:
        p = pattern.strip().lower()
        if not p:
            continue
        if not p.startswith("*") and not p.endswith("*"):
            p = f"*{p}*"
        if fnmatch.fnmatch(target_norm, p) or fnmatch.fnmatch(target.lower(), p):
            return True
    return False


def get_existing_stems_on_disk(download_dir: Path) -> set:
    stems = set()
    if not download_dir.exists():
        return stems
    for path in download_dir.rglob("*"):
        # Ignore empty/dummy or HTML error files under 50KB
        if path.is_file() and path.stat().st_size > 50000:
            norm = re.sub(r"[^a-zA-Z0-9]", "", path.stem.lower())
            if norm:
                stems.add(norm)
    return stems


def apply_heuristics(
    raw_items: List[Dict[str, Any]],
    download_dir: Path,
    exclusions: Optional[str] = "",
) -> List[Dict[str, Any]]:
    custom_patterns = [p.strip() for p in (exclusions or "").split(",") if p.strip()]
    active_patterns = list(set(DEFAULT_EXCLUDE_PATTERNS + custom_patterns))

    existing_stems = get_existing_stems_on_disk(download_dir)
    plan = []

    for item in raw_items:
        original_path = item.get("path", item.get("name", ""))
        original_name = item.get("name", "")
        file_id = item.get("id")
        size = item.get("size", 0)

        sanitized_rel_path = sanitize_path(original_path)
        sanitized_name = Path(sanitized_rel_path).name

        plan_entry = {
            "id": file_id,
            "original_path": original_path,
            "sanitized_path": sanitized_rel_path,
            "name": sanitized_name,
            "size": size,
            "action": "download",
            "reason": "Ready for download",
        }

        if (
            matches_wildcards(original_name, active_patterns)
            or matches_wildcards(original_path, active_patterns)
            or matches_wildcards(sanitized_name, active_patterns)
        ):
            plan_entry["action"] = "skip"
            plan_entry["reason"] = "Matched bloat / exclusion pattern"
            plan.append(plan_entry)
            continue

        norm_stem = re.sub(r"[^a-zA-Z0-9]", "", Path(sanitized_name).stem.lower())
        if norm_stem in existing_stems:
            plan_entry["action"] = "skip"
            plan_entry["reason"] = "File already exists on local disk"
            plan.append(plan_entry)
            continue

        plan.append(plan_entry)

    return plan


def is_valid_binary_file(file_path: Path) -> bool:
    """Verifies the downloaded file is real binary data and not a Google Drive HTML error page."""
    if not file_path.exists() or file_path.stat().st_size < 1024:
        return False
    try:
        with open(file_path, "rb") as f:
            header = f.read(512)
        # If it starts with HTML / DOCTYPE, Google served a rate-limit/warning block
        if b"<!DOCTYPE" in header or b"<html" in header or b"<!doctype" in header:
            return False
        return True
    except Exception:
        return False


def gdown_download_file(file_id: str, destination: Path, cancel_event: asyncio.Event) -> tuple[bool, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_target = destination.with_suffix(destination.suffix + ".part")

    # Use direct sharing view link which triggers gdown's full confirmation scraper
    urls_to_try = [
        f"https://drive.google.com/uc?id={file_id}",
        f"https://drive.google.com/file/d/{file_id}/view?usp=sharing",
    ]

    for url in urls_to_try:
        if cancel_event.is_set():
            return False, "Cancelled by user"

        try:
            output = gdown.download(
                url=url,
                output=str(temp_target),
                quiet=False,
                fuzzy=True,
                use_cookies=False,
            )

            if cancel_event.is_set():
                if temp_target.exists():
                    temp_target.unlink()
                return False, "Cancelled by user"

            # Check if gdown wrote to temp_target or to a different output name
            resolved_file = Path(output) if output else temp_target

            if resolved_file.exists() and is_valid_binary_file(resolved_file):
                if destination.exists():
                    destination.unlink()
                resolved_file.rename(destination)
                return True, "OK"
            else:
                # If Google returned an HTML page (quota exceeded / virus warning)
                if resolved_file.exists():
                    try:
                        content_sample = resolved_file.read_text(errors="ignore")[:300]
                        resolved_file.unlink()
                        if "quota" in content_sample.lower():
                            return False, "Google Drive quota exceeded for this file"
                        if "virus" in content_sample.lower():
                            continue # Try sharing URL fallback
                    except Exception:
                        if resolved_file.exists():
                            resolved_file.unlink()
        except Exception as e:
            if temp_target.exists():
                try:
                    temp_target.unlink()
                except Exception:
                    pass
            continue

    return False, "Google Drive rate limit or virus-warning block (file unretrievable via public token)"


async def download_folder_plan(
    item: Dict[str, Any],
    target_path: Path,
    cancel_event: asyncio.Event,
) -> tuple[bool, str]:
    if cancel_event.is_set():
        return False, "Cancelled"

    file_id = item.get("id")
    if not file_id:
        return False, "Missing file ID"

    loop = asyncio.get_event_loop()
    task = loop.run_in_executor(None, gdown_download_file, file_id, target_path, cancel_event)

    while not task.done():
        if cancel_event.is_set():
            if target_path.exists():
                try:
                    target_path.unlink()
                except Exception:
                    pass
            return False, "Cancelled"
        await asyncio.sleep(0.4)

    return await task


def repair_extensions(file_path: Path) -> Path:
    if not file_path.exists() or file_path.stat().st_size < 16:
        return file_path

    if file_path.suffix and re.match(r"^\.[a-zA-Z0-9]{2,5}$", file_path.suffix):
        return file_path

    try:
        with open(file_path, "rb") as f:
            header = f.read(64)
    except Exception:
        return file_path

    detected_ext = None
    for offset, magic, ext in MAGIC_BYTE_SIGNATURES:
        if len(header) >= offset + len(magic):
            if header[offset : offset + len(magic)] == magic:
                detected_ext = ext
                break

    if detected_ext:
        new_path = file_path.with_name(f"{file_path.name}{detected_ext}")
        try:
            file_path.rename(new_path)
            return new_path
        except Exception:
            return file_path

    return file_path
