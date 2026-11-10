import json
import os
import selectors
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from backend.core import (
    DOWNLOAD_DIR,
    check_file_on_disk,
    format_size,
    matching_exclusions,
    sanitize_destination_name,
    sanitize_path_tree,
)

MEGA_HELPER = os.environ.get("MEGA_HELPER_PATH", "/usr/local/bin/aetherdrive-mega")
MAX_SCAN_ITEMS = 2000


def validate_mega_link(url: str) -> str:
    link = url.strip()
    if not link or len(link) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in link):
        raise ValueError("The MEGA link is invalid.")
    try:
        parsed = urlsplit(link)
        port = parsed.port
    except ValueError as err:
        raise ValueError("The MEGA link is invalid.") from err
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"mega.nz", "www.mega.nz", "mega.co.nz", "www.mega.co.nz"}
        or parsed.username
        or parsed.password
        or port is not None
    ):
        raise ValueError("Enter an HTTPS public MEGA file or folder link.")

    path_parts = [part for part in parsed.path.split("/") if part]
    has_path_link = (
        len(path_parts) >= 2
        and path_parts[0].lower() in {"file", "folder"}
        and bool(path_parts[1])
    )
    fragment_parts = parsed.fragment.split("!")
    has_legacy_link = (
        len(fragment_parts) >= 3
        and fragment_parts[0] in {"", "F"}
        and bool(fragment_parts[1])
        and bool(fragment_parts[2])
    )
    query = parse_qs(parsed.query)
    has_key = bool(parsed.fragment) or bool(query.get("key"))
    if not ((has_path_link and has_key) or has_legacy_link):
        raise ValueError("The MEGA link is incomplete or is not a public file or folder link.")
    return link


def _helper_error(stdout: str, stderr: str, returncode: int) -> str:
    try:
        result = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        result = {}
    if isinstance(result, dict) and result.get("event") == "error":
        return str(result.get("message") or "MEGA SDK operation failed.")
    if stderr.strip():
        return stderr.strip()[:500]
    return f"MEGA SDK operation failed (exit code {returncode})."


def _run_inspection(link: str) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            [MEGA_HELPER, "inspect"],
            input=link + "\n",
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except FileNotFoundError as err:
        raise RuntimeError("The MEGA SDK helper is unavailable in this installation.") from err
    except subprocess.TimeoutExpired as err:
        raise RuntimeError("MEGA inspection timed out. Try again later.") from err
    if result.returncode:
        raise ValueError(_helper_error(result.stdout, result.stderr, result.returncode))
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as err:
        raise RuntimeError("The MEGA SDK helper returned an invalid inspection result.") from err
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise RuntimeError("The MEGA SDK helper returned an incomplete inspection result.")
    return response


def inspect_mega_link(
    url: str, exclusions: Optional[List[str]] = None
) -> Dict[str, Any]:
    link = validate_mega_link(url)
    response = _run_inspection(link)
    root_name = str(response.get("name") or "").strip()
    source_kind = response.get("kind")
    remote_items = response["items"]
    if not root_name or source_kind not in {"file", "folder"}:
        raise RuntimeError("The MEGA SDK helper returned invalid source metadata.")
    if not remote_items:
        raise ValueError("No downloadable files were found in this MEGA link.")
    if len(remote_items) > MAX_SCAN_ITEMS:
        raise ValueError(f"The MEGA link contains more than {MAX_SCAN_ITEMS:,} files.")

    if "/" in root_name or "\\" in root_name or any(
        ord(char) < 32 or ord(char) == 127 for char in root_name
    ):
        raise RuntimeError("The MEGA source has an invalid name.")
    destination_source_name = Path(root_name).stem if source_kind == "file" else root_name
    destination = sanitize_destination_name(destination_source_name)
    if (
        Path(destination).is_absolute()
        or destination in {"", ".", ".."}
        or "/" in destination
        or "\\" in destination
        or any(ord(char) < 32 or ord(char) == 127 for char in destination)
    ):
        destination = "AetherDrive_Download"
    items = []
    seen_handles = set()
    for remote_item in remote_items:
        try:
            handle = str(remote_item["handle"])
            raw_path = str(remote_item["path"])
            size = int(remote_item["size"])
        except (KeyError, TypeError, ValueError) as err:
            raise RuntimeError("The MEGA SDK helper returned invalid file metadata.") from err
        if (
            not handle.isdigit()
            or size < 0
            or not raw_path
            or raw_path.startswith("/")
            or "\\" in raw_path
            or any(
                part in {"", ".", ".."}
                or any(ord(char) < 32 or ord(char) == 127 for char in part)
                for part in raw_path.split("/")
            )
        ):
            raise RuntimeError("The MEGA SDK helper returned an unsafe file path.")
        if handle in seen_handles:
            continue
        seen_handles.add(handle)

        clean_path = sanitize_path_tree(raw_path)
        if Path(clean_path).is_absolute() or any(
            part in {"", ".", ".."} for part in Path(clean_path).parts
        ):
            raise ValueError("A MEGA file has a path that cannot be safely downloaded.")
        bloat_reasons = matching_exclusions(raw_path, exclusions)
        on_disk = (
            check_file_on_disk(destination, clean_path, size)
            if not bloat_reasons
            else False
        )
        category = "bloat" if bloat_reasons else ("on_disk" if on_disk else "approved")
        items.append({
            "id": f"mega:{handle}",
            "mega_handle": handle,
            "source_type": "mega",
            "original_path": raw_path,
            "sanitized_path": clean_path,
            "size": size,
            "size_str": format_size(size),
            "category": category,
            "bloat_reasons": bloat_reasons,
            "selected": category == "approved",
        })

    if not items:
        raise ValueError("No downloadable files were found in this MEGA link.")
    return {
        "destination_folder": destination,
        "source_folder_name": destination,
        "destination_exists_on_disk": (DOWNLOAD_DIR / destination).exists(),
        "cookie_active": False,
        "source_type": "mega",
        "items": items,
    }


def download_mega_file(
    url: str,
    handle: str,
    output: Path,
    on_progress: Callable[[int, Optional[int]], None],
) -> str:
    link = validate_mega_link(url)
    if not handle.isdigit():
        raise ValueError("The MEGA file handle is invalid.")
    try:
        process = subprocess.Popen(
            [MEGA_HELPER, "download", handle, str(output)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as err:
        raise RuntimeError("The MEGA SDK helper is unavailable in this installation.") from err

    try:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Failed to connect to the MEGA SDK helper.")
        process.stdin.write(link + "\n")
        process.stdin.close()

        def handle_event(line: str):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as err:
                raise RuntimeError("The MEGA SDK helper returned invalid progress data.") from err
            if event.get("event") == "progress":
                downloaded = int(event.get("downloaded", 0))
                total = event.get("total")
                on_progress(downloaded, int(total) if total is not None else None)
            elif event.get("event") == "error":
                raise ValueError(str(event.get("message") or "MEGA download failed."))
            else:
                raise RuntimeError("The MEGA SDK helper returned an unknown progress event.")

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                for key, _ in selector.select(timeout=0.25):
                    line = key.fileobj.readline()
                    if not line:
                        selector.unregister(key.fileobj)
                        continue
                    handle_event(line)
                if process.poll() is not None:
                    for line in process.stdout:
                        handle_event(line)
                    break
        finally:
            selector.close()
        return_code = process.wait()
        if return_code:
            raise RuntimeError(f"MEGA SDK download failed (exit code {return_code}).")
    except BaseException:
        if process.poll() is None:
            process.send_signal(2)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait()
        raise
    finally:
        if process.stdout:
            process.stdout.close()
    return str(output)
