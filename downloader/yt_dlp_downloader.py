import downloader.file_manager as fm
import downloader.url_parser as up
import config.settings as s
import storage.state as st
import drive.upload as u
import drive.client as cl
import utils.likes as lk

from yt_dlp import YoutubeDL
from pathlib import Path

class DuplicateVideoError(Exception):
    def __init__(self, video_number: str, author: str, category: str) -> None:
        self.video_number = video_number
        self.author = author
        self.category = category
        super().__init__(f"Video already downloaded as {video_number}.mp4")

def download_video(url: str) -> dict[str, str]:
    if not cl.DRIVE_SERVICE:
        raise RuntimeError("Google Drive is not configured. Check OAuth variables in .env.")

    s.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temp_video_dir = fm.get_temp_video_dir()
    temp_video_dir.mkdir(parents=True, exist_ok=True)

    duplicate = st.find_duplicate_by_url(url)
    if duplicate:
        raise DuplicateVideoError(
            duplicate["number"],
            duplicate["author"],
            duplicate.get("category", ""),
        )

    info_options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with YoutubeDL(info_options) as ydl:
        info = ydl.extract_info(url, download=False)

    video_key = up.get_video_key(info, url)
    downloaded_videos = st.get_downloaded_videos()
    if video_key in downloaded_videos:
        saved_video = downloaded_videos[video_key]
        raise DuplicateVideoError(
            saved_video["number"],
            saved_video["author"],
            saved_video.get("category", ""),
        )

    like_count = lk.get_like_count(info)
    options = {
        "outtmpl": str(temp_video_dir / "%(id)s.%(ext)s"),
        "format": (
            "bv*[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/"
            "bv*[vcodec^=h264][ext=mp4]+ba[ext=m4a]/"
            "b[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    before = set(temp_video_dir.glob("*"))
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        prepared = Path(ydl.prepare_filename(info))

    like_count = lk.get_like_count(info) or like_count
    category = lk.get_like_category(like_count)
    video_number = st.get_next_video_number(category)
    author = (
        info.get("uploader")
        or info.get("channel")
        or info.get("creator")
        or info.get("uploader_id")
        or "невідомо"
    )
    author_url = info.get("uploader_url") or info.get("channel_url") or ""
    final_url = info.get("webpage_url") or url

    after = set(temp_video_dir.glob("*"))
    new_files = sorted(after - before, key=lambda item: item.stat().st_mtime, reverse=True)

    if new_files:
        file_path = fm.ensure_video_file_name(new_files[0], video_number, like_count)
        return u.finish_downloaded_file(
            file_path,
            video_key,
            video_number,
            author,
            author_url,
            final_url,
            like_count,
            category,
        )

    if prepared.exists():
        file_path = fm.ensure_video_file_name(prepared, video_number, like_count)
        return u.finish_downloaded_file(
            file_path,
            video_key,
            video_number,
            author,
            author_url,
            final_url,
            like_count,
            category,
        )

    mp4_file = prepared.with_suffix(".mp4")
    if mp4_file.exists():
        file_path = fm.ensure_video_file_name(mp4_file, video_number, like_count)
        return u.finish_downloaded_file(
            file_path,
            video_key,
            video_number,
            author,
            author_url,
            final_url,
            like_count,
            category,
        )

    raise FileNotFoundError("Video was downloaded, but the output file was not found.")