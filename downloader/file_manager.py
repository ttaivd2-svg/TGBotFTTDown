import utils.health_server as hs
import config.settings as s
import utils.naming as n
from pathlib import Path

def get_state_dir(category: dict) -> Path:
    if category["key"] == s.ROOT_CATEGORY["key"]:
        return s.DOWNLOAD_DIR
    return s.DOWNLOAD_DIR / category["key"]

def get_temp_video_dir() -> Path:
    return s.DOWNLOAD_DIR / "tmp"

def delete_local_video_file(file_path: Path) -> None:
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        hs.logger.warning("Could not delete temporary video file: %s", file_path, exc_info=True)

def ensure_video_file_name(file_path: Path, video_number: int, like_count: int | None) -> Path:
    target_path = file_path.with_name(f"{n.get_video_file_stem(video_number, like_count)}{file_path.suffix}")
    if file_path == target_path:
        return file_path

    try:
        if target_path.exists():
            target_path.unlink()
        return file_path.rename(target_path)
    except OSError:
        hs.logger.warning("Could not rename temporary video file: %s", file_path, exc_info=True)
        return file_path