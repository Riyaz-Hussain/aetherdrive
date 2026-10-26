import os
import re
import fnmatch
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

import gdown

# Magic byte signatures for post-download extension repair
MAGIC_BYTE_SIGNATURES = [
    # MP4 variants: offset 4 ftyp
    (4, b"ftyp", ".mp4"),
    # MKV / WebM: starts with 1A 45 DF A3
    (0, b"\x1a\x45\xdf\xa3", ".mkv"),
    # AVI: starts with RIFF, offset 8 AVI 
    (0, b"RIFF", ".avi"),
    # ZIP archives (including jar, apk, docx, etc. if generic)
    (0, b"PK\x03\x04", ".zip"),
    # RAR
    (0, b"Rar!\x1a\x07", ".rar"),
    # 7-Zip
    (0, b"7z\xbc\xaf\x27\x1c", ".7z"),
    # PDF
    (0, b"%PDF", ".pdf"),
    # PNG
    (0, b"\x89PNG\r\n\x1a\n", ".png"),
    # JPEG
    (0, b"\xff\xd8\xff", ".jpg"),
    # MP3 ID3 header
    (0, b"ID3", ".mp3"),
]

# Default bloat & spam patterns
DEFAULT_EXCLUDE_PATTERNS = [
    "*500$*",
    "*all in one vip*",
    "*bundle*",
    "*free course*",
    "*join telegram*",
    "*join whatsapp*",
    "*vip access*",
    "*bonus*",
    "*reels bundle*",
    "*giveaway*",
    "*crack*",
    "*keygen*",
]

# Known domain extensions to strip from folder/file paths
URL_DOMAIN_PATTERN = re.compile(
    r"(?:https?:\/\/)?(?:www\.)?[a-zA-Z0-9_\-]+\.(?:vercel\.app|blogspot\.com|com|net|org|io|me|xyz|top|live|site|info|online)\b",
    re.IGNORECASE,
)
SOCIAL_HANDLE_PATTERN = re.compile(r"@[a-zA-Z0-9_]+", re.IGNORECASE)


def extract_drive_folder_id(url: str) -> Optional[str]:
    """Extracts the Google Drive folder ID from various URL formats."""
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
    """Inspects a public Google Drive folder tree using gdown's folder parser."""
    folder_id = extract_drive_folder_id(folder_url)
    if not folder_id:
        raise ValueError("Invalid Google Drive folder link or folder ID provided.")

    loop = asyncio.get_event_loop()

    def _fetch_metadata():
        url = f"https://drive.google.com/drive/folders/{folder_id}"
        # skip_download=True instructs gdown to crawl and resolve the tree without fetching binaries
        items = gdown.download_folder(url, skip_download=True, quiet=True, use_cookies=False)
        return items

    raw_items = await loop.run_in_executor(None, _fetch_metadata)

    if not raw_items:
        raise ValueError("No accessible items found in this Google Drive folder. Verify the folder link is public.")

    processed = []
    for item in raw_items:
        # Check if item is an object or a path dict
        if isinstance(item, dict):
            if not item.get("is_folder") and item.get("id"):
                processed.append({
                    "id": item["id"],
                    "name": item.get("name", "unnamed_file"),
                    "path": item.get("path", item.get("name", "unnamed_file")),
                    "size": item.get("size", 0),
                })
        elif hasattr(item, "id"):
            processed.append({
                "id": getattr(item, "id"),
                "name": getattr(item, "name", "unnamed_file"),
                "path": getattr(item, "path", getattr(item, "name", "unnamed_file")),
                "size": getattr(item, "size", 0),
            })
        elif isinstance(item, str):
            # When file paths are returned as resolved strings
            processed.append({
                "id": folder_id,
                "name": Path(item).name,
                "path": item,
                "size": 0,
            })

    return processed


def sanitize_path(raw_path: str) -> str:
    """Strips promotional URLs, @handles, domain names, and bracket noise from a path."""
    parts = raw_path.replace("\\", "/").strip("/").split("/")
    cleaned_parts = []

    for part in parts:
        # Strip URLs and domains
        s = URL_DOMAIN_PATTERN.sub("", part)
        # Strip social handles
        s = SOCIAL_HANDLE_PATTERN.sub("", s)
        # Strip excess symbols and surrounding brackets like [BestCourses]
        s = re.sub(r"^\[.*?\]\s*", "", s)
        s = re.sub(r"\(.*?\.(?:com|org|net|app|io|xyz)\)", "", s, flags=re.IGNORECASE)
        # Clean double spaces, underscores, and stray dashes
        s = re.sub(r"[_\s]+", " ", s)
        s = re.sub(r"^[-\s]+|[-\s]+$", "", s)

        # Fallback if cleaning completely emptied the name
        if not s.strip():
            s = part.strip()
        cleaned_parts.append(s.strip())

    # Collapse duplicated identical consecutive parent-child folders
    collapsed = []
    for part in cleaned_parts:
        if not collapsed or collapsed[-1].lower() != part.lower():
            collapsed.append(part)

    return "/".join(collapsed)


def matches_wildcards(target: str, patterns: List[str]) -> bool:
    """Tests if a given string matches any wildcard pattern (case-insensitive)."""
    target_lower = target.lower()
    for pattern in patterns:
        p = pattern.strip().lower()
        if not p:
            continue
        if not p.startswith("*") and not p.endswith("*"):
            p = f"*{p}*"
        if fnmatch.fnmatch(target_lower, p):
            return True
    return False


def get_existing_stems_on_disk(download_dir: Path) -> set:
    """Pre-caches filename stems already on disk to enable fast idempotent checking."""
    stems = set()
    if not download_dir.exists():
        return stems
    for path in download_dir.rglob("*"):
        if path.is_file():
            # Normalized clean stem (lower-case alphanumeric only)
            norm = re.sub(r"[^a-zA-Z0-9]", "", path.stem.lower())
            if norm:
                stems.add(norm)
    return stems


def apply_heuristics(
    raw_items: List[Dict[str, Any]],
    download_dir: Path,
    exclude_folders: Optional[str] = "",
    exclude_files: Optional[str] = "",
) -> List[Dict[str, Any]]:
    """Analyzes raw Drive metadata, runs exclusions, cleans paths, and tags actions."""
    folder_patterns = [p.strip() for p in (exclude_folders or "").split(",") if p.strip()]
    file_patterns = [p.strip() for p in (exclude_files or "").split(",") if p.strip()]

    # If user provided no custom file filters, combine with defaults
    combined_file_patterns = list(set(DEFAULT_EXCLUDE_PATTERNS + file_patterns))

    existing_stems = get_existing_stems_on_disk(download_dir)
    plan = []

    for item in raw_items:
        original_path = item.get("path", item.get("name", ""))
        original_name = item.get("name", "")
        file_id = item.get("id")
        size = item.get("size", 0)

        # Extract parent directory relative path
        parent_dir = str(Path(original_path).parent)
        if parent_dir == ".":
            parent_dir = ""

        sanitized_rel_path = sanitize_path(original_path)
        sanitized_name = Path(sanitized_rel_path).name

        plan_entry = {
            "id": file_id,
            "original_path": original_path,
            "sanitized_path": sanitized_rel_path,
            "name": sanitized_name,
            "size": size,
            "action": "download",
            "reason": "Ready for ingestion",
        }

        # 1. Check folder-level exclude rules
        if parent_dir and folder_patterns and matches_wildcards(parent_dir, folder_patterns):
            plan_entry["action"] = "skip"
            plan_entry["reason"] = "Folder matched exclusion pattern"
            plan.append(plan_entry)
            continue

        # 2. Check file-level exclude rules (name and full path)
        if matches_wildcards(original_name, combined_file_patterns) or matches_wildcards(original_path, combined_file_patterns):
            plan_entry["action"] = "skip"
            plan_entry["reason"] = "File matched promotional/bloat filter"
            plan.append(plan_entry)
            continue

        # 3. Check idempotent disk sync (avoid re-downloading)
        norm_stem = re.sub(r"[^a-zA-Z0-9]", "", Path(sanitized_name).stem.lower())
        if norm_stem in existing_stems:
            plan_entry["action"] = "skip"
            plan_entry["reason"] = "File already exists on local disk"
            plan.append(plan_entry)
            continue

        plan.append(plan_entry)

    return plan


async def download_folder_plan(
    item: Dict[str, Any],
    target_path: Path,
    cancel_event: asyncio.Event,
) -> bool:
    """Downloads a single approved Drive file in an asynchronous thread with cancel listening."""
    if cancel_event.is_set():
        return False

    file_id = item.get("id")
    if not file_id:
        return False

    url = f"https://drive.google.com/uc?id={file_id}"
    loop = asyncio.get_event_loop()

    def _sync_download():
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            # Use gdown to download directly to destination
            gdown.download(url, str(target_path), quiet=True, fuzzy=True)
            return target_path.exists() and target_path.stat().st_size > 0
        except Exception:
            return False

    download_task = loop.run_in_executor(None, _sync_download)

    # Monitor cancellation status while the download task runs
    while not download_task.done():
        if cancel_event.is_set():
            # If aborted while downloading, clean up incomplete binary residue
            if target_path.exists():
                try:
                    target_path.unlink()
                except Exception:
                    pass
            return False
        await asyncio.sleep(0.3)

    return await download_task


def repair_extensions(file_path: Path) -> Path:
    """Reads initial binary header bytes to restore missing or truncated file extensions."""
    if not file_path.exists() or file_path.stat().st_size < 16:
        return file_path

    # If the file already has an identifiable 3-4 char alphanumeric extension, keep it
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