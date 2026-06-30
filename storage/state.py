import downloader.file_manager as fm
import downloader.url_parser as up
import utils.health_server as hs
import config.settings as s
import drive.metadata as m
import drive.upload as u
import utils.likes as lk

from pathlib import Path

def save_video_author(file_path: Path, author: str, like_count: int | None, category: dict) -> None:
    state_dir = fm.get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    authors_file = state_dir / s.AUTHORS_FILE_NAME

    with authors_file.open("a", encoding="utf-8") as file:
        file.write(
            f"{file_path.stem} - {author} - likes: {lk.format_like_count(like_count)}\n"
        )

    u.sync_state_file_to_drive(s.AUTHORS_FILE_NAME, category)

def iter_downloaded_video_files():
    for category in s.ALL_CATEGORIES:
        yield category, fm.get_state_dir(category) / s.DOWNLOADED_FILE_NAME


def get_downloaded_videos() -> dict[str, dict[str, str]]:
    videos = {}

    for category, downloaded_file in iter_downloaded_video_files():
        if not downloaded_file.exists():
            continue

        with downloaded_file.open("r", encoding="utf-8") as file:
            for line in file:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue

                key, number, author, url = parts[:4]
                like_count = parts[4] if len(parts) >= 5 else ""
                videos[key] = {
                    "number": number,
                    "author": author,
                    "url": url,
                    "like_count": like_count,
                    "category": lk.get_category_label(category),
                }

    return videos

def save_downloaded_video(
    key: str,
    file_path: Path,
    author: str,
    url: str,
    like_count: int | None,
    category: dict,
) -> None:
    state_dir = fm.get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    downloaded_file = state_dir / s.DOWNLOADED_FILE_NAME

    with downloaded_file.open("a", encoding="utf-8") as file:
        file.write(
            f"{key}\t{file_path.stem}\t{author}\t{up.normalize_url(url)}\t"
            f"{lk.format_like_count(like_count)}\n"
        )

    u.sync_state_file_to_drive(s.DOWNLOADED_FILE_NAME, category)


def get_next_video_number(category: dict) -> int:
    state_dir = fm.get_state_dir(category)
    counter_file = state_dir / s.NEXT_NUMBER_FILE_NAME
    next_number = 1

    if not counter_file.exists():
        next_number = 1
    else:
        try:
            next_number = int(counter_file.read_text(encoding="utf-8").strip())
        except ValueError:
            hs.logger.warning("Invalid counter file ignored: %s", counter_file)
            next_number = 1

    next_number = max(next_number, 1)

    try:
        folder_id = m.get_category_drive_folder_id(category)
        drive_numbers = m.get_drive_video_numbers(folder_id)
    except Exception:
        hs.logger.warning(
            "Could not check Google Drive folder numbers for %s",
            lk.get_category_label(category),
            exc_info=True,
        )
        drive_numbers = []

    if drive_numbers:
        next_number = max(next_number, max(drive_numbers) + 1)

    return next_number


def save_next_video_number(category: dict, next_number: int) -> None:
    state_dir = fm.get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    counter_file = state_dir / s.NEXT_NUMBER_FILE_NAME
    counter_file.write_text(f"{next_number}\n", encoding="utf-8")
    u.sync_state_file_to_drive(s.NEXT_NUMBER_FILE_NAME, category)

def find_duplicate_by_url(url: str) -> dict[str, str] | None:
    normalized_url = up.normalize_url(url)
    for video in get_downloaded_videos().values():
        if up.normalize_url(video["url"]) == normalized_url:
            return video

    return None