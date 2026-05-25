import asyncio
import io
import logging
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import credentials as oauth_credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from yt_dlp import YoutubeDL


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads")).expanduser().resolve()
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REFRESH_TOKEN = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()
RETRY_DELAYS_SECONDS = os.getenv("RETRY_DELAYS_SECONDS", "300,900,1800").strip()
PORT = int(os.getenv("PORT", "10000"))

AUTHORS_FILE_NAME = "authors.txt"
DOWNLOADED_FILE_NAME = "downloaded_videos.txt"
NEXT_NUMBER_FILE_NAME = "next_number.txt"
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
VIDEO_NUMBER_RE = re.compile(r"^(\d+)(?:\s+-|\.)")
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
VIDEO_FILE_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
DOWNLOAD_LOCK = asyncio.Lock()

ROOT_CATEGORY = {
    "key": "root",
    "label": "Under 1M likes / unknown",
    "folder_name": None,
    "min_likes": 0,
}
LIKE_CATEGORIES = [
    {
        "key": "10m",
        "label": "10M+ likes",
        "folder_name": "10M+ likes",
        "min_likes": 10_000_000,
    },
    {
        "key": "1m",
        "label": "1M+ likes",
        "folder_name": "1M+ likes",
        "min_likes": 1_000_000,
    },
]
ALL_CATEGORIES = [ROOT_CATEGORY, *LIKE_CATEGORIES]
DRIVE_FOLDER_CACHE: dict[str, str] = {}


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/", "/health"}:
            self.send_response(404)
            self.end_headers()
            return

        body = b"OK\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        logger.debug("Health check: " + format, *args)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


def get_drive_service():
    if not (
        GOOGLE_DRIVE_FOLDER_ID
        and GOOGLE_OAUTH_CLIENT_ID
        and GOOGLE_OAUTH_CLIENT_SECRET
        and GOOGLE_OAUTH_REFRESH_TOKEN
    ):
        return None

    credentials = oauth_credentials.Credentials(
        token=None,
        refresh_token=GOOGLE_OAUTH_REFRESH_TOKEN,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=DRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


DRIVE_SERVICE = get_drive_service()


def start_health_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health check server started on port %s", PORT)


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0) if match else None


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def get_retry_delays() -> list[int]:
    delays = []
    for item in RETRY_DELAYS_SECONDS.split(","):
        item = item.strip()
        if not item:
            continue

        try:
            delay = int(item)
        except ValueError:
            logger.warning("Invalid retry delay ignored: %s", item)
            continue

        if delay > 0:
            delays.append(delay)

    return delays


def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "too many requests" in text


def format_delay(delay: int) -> str:
    minutes, seconds = divmod(delay, 60)
    if minutes and seconds:
        return f"{minutes} хв {seconds} сек"
    if minutes:
        return f"{minutes} хв"
    return f"{seconds} сек"


def get_drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def get_like_count(info: dict) -> int | None:
    like_count = info.get("like_count")
    return like_count if isinstance(like_count, int) and like_count >= 0 else None


def format_like_count(like_count: int | None) -> str:
    if like_count is None:
        return "невідомо"
    return str(like_count)


def get_like_category(like_count: int | None) -> dict:
    if like_count is None:
        return ROOT_CATEGORY

    for category in LIKE_CATEGORIES:
        if like_count >= category["min_likes"]:
            return category

    return ROOT_CATEGORY


def get_video_file_stem(video_number: int, like_count: int | None) -> str:
    if like_count is None:
        return f"{video_number} - likes_unknown"
    return f"{video_number} - {like_count} likes"


def get_state_dir(category: dict) -> Path:
    if category["key"] == ROOT_CATEGORY["key"]:
        return DOWNLOAD_DIR
    return DOWNLOAD_DIR / category["key"]


def get_temp_video_dir() -> Path:
    return DOWNLOAD_DIR / "tmp"


def get_category_label(category: dict) -> str:
    return str(category["label"])


def delete_local_video_file(file_path: Path) -> None:
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete temporary video file: %s", file_path, exc_info=True)


def ensure_video_file_name(file_path: Path, video_number: int, like_count: int | None) -> Path:
    target_path = file_path.with_name(f"{get_video_file_stem(video_number, like_count)}{file_path.suffix}")
    if file_path == target_path:
        return file_path

    try:
        if target_path.exists():
            target_path.unlink()
        return file_path.rename(target_path)
    except OSError:
        logger.warning("Could not rename temporary video file: %s", file_path, exc_info=True)
        return file_path


def escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_drive_file_id(
    name: str,
    parent_id: str,
    mime_type: str | None = None,
) -> str | None:
    if not DRIVE_SERVICE:
        return None

    safe_name = escape_drive_query_value(name)
    query = (
        f"name = '{safe_name}' and "
        f"'{parent_id}' in parents and "
        "trashed = false"
    )
    if mime_type:
        query += f" and mimeType = '{escape_drive_query_value(mime_type)}'"

    response = (
        DRIVE_SERVICE.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
        )
        .execute()
    )
    files = response.get("files", [])
    return files[0]["id"] if files else None


def get_drive_video_numbers(parent_id: str) -> list[int]:
    if not DRIVE_SERVICE:
        return []

    numbers = []
    page_token = None
    query = f"'{parent_id}' in parents and trashed = false"

    while True:
        response = (
            DRIVE_SERVICE.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(name)",
                pageSize=1000,
                pageToken=page_token,
            )
            .execute()
        )

        for file in response.get("files", []):
            name = file.get("name", "")
            if Path(name).suffix.lower() not in VIDEO_FILE_SUFFIXES:
                continue

            match = VIDEO_NUMBER_RE.match(name)
            if match:
                numbers.append(int(match.group(1)))

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return numbers


def get_category_drive_folder_id(category: dict) -> str:
    if category["key"] == ROOT_CATEGORY["key"]:
        return GOOGLE_DRIVE_FOLDER_ID

    cache_key = str(category["key"])
    if cache_key in DRIVE_FOLDER_CACHE:
        return DRIVE_FOLDER_CACHE[cache_key]

    folder_name = str(category["folder_name"])
    folder_id = find_drive_file_id(
        folder_name,
        GOOGLE_DRIVE_FOLDER_ID,
        mime_type=DRIVE_FOLDER_MIME_TYPE,
    )
    if not folder_id:
        metadata = {
            "name": folder_name,
            "mimeType": DRIVE_FOLDER_MIME_TYPE,
            "parents": [GOOGLE_DRIVE_FOLDER_ID],
        }
        result = DRIVE_SERVICE.files().create(body=metadata, fields="id").execute()
        folder_id = result["id"]

    DRIVE_FOLDER_CACHE[cache_key] = folder_id
    return folder_id


def download_drive_text_file(name: str, category: dict) -> None:
    parent_id = get_category_drive_folder_id(category)
    file_id = find_drive_file_id(name, parent_id)
    if not DRIVE_SERVICE or not file_id:
        return

    state_dir = get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    request = DRIVE_SERVICE.files().get_media(fileId=file_id)
    with io.FileIO(state_dir / name, "wb") as file_handle:
        downloader = MediaIoBaseDownload(file_handle, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()


def upload_to_drive(
    file_path: Path,
    parent_id: str,
    name: str | None = None,
    replace: bool = False,
) -> str:
    if not DRIVE_SERVICE:
        raise RuntimeError("Google Drive is not configured.")

    upload_name = name or file_path.name
    mime_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)

    if replace:
        file_id = find_drive_file_id(upload_name, parent_id)
        if file_id:
            result = (
                DRIVE_SERVICE.files()
                .update(fileId=file_id, media_body=media, fields="id")
                .execute()
            )
            return result["id"]

    metadata = {"name": upload_name, "parents": [parent_id]}
    result = (
        DRIVE_SERVICE.files()
        .create(body=metadata, media_body=media, fields="id")
        .execute()
    )
    return result["id"]


def sync_state_from_drive() -> None:
    for category in ALL_CATEGORIES:
        get_category_drive_folder_id(category)
        download_drive_text_file(AUTHORS_FILE_NAME, category)
        download_drive_text_file(DOWNLOADED_FILE_NAME, category)
        download_drive_text_file(NEXT_NUMBER_FILE_NAME, category)


def sync_state_file_to_drive(name: str, category: dict) -> None:
    state_dir = get_state_dir(category)
    file_path = state_dir / name
    if file_path.exists():
        upload_to_drive(
            file_path,
            parent_id=get_category_drive_folder_id(category),
            name=name,
            replace=True,
        )


def save_video_author(file_path: Path, author: str, like_count: int | None, category: dict) -> None:
    state_dir = get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    authors_file = state_dir / AUTHORS_FILE_NAME

    with authors_file.open("a", encoding="utf-8") as file:
        file.write(
            f"{file_path.stem} - {author} - likes: {format_like_count(like_count)}\n"
        )

    sync_state_file_to_drive(AUTHORS_FILE_NAME, category)


class DuplicateVideoError(Exception):
    def __init__(self, video_number: str, author: str, category: str) -> None:
        self.video_number = video_number
        self.author = author
        self.category = category
        super().__init__(f"Video already downloaded as {video_number}.mp4")


def get_video_key(info: dict, fallback_url: str) -> str:
    extractor = info.get("extractor_key") or info.get("extractor") or "unknown"
    video_id = info.get("id") or info.get("webpage_url") or fallback_url
    return f"{extractor}:{video_id}"


def iter_downloaded_video_files():
    for category in ALL_CATEGORIES:
        yield category, get_state_dir(category) / DOWNLOADED_FILE_NAME


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
                    "category": get_category_label(category),
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
    state_dir = get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    downloaded_file = state_dir / DOWNLOADED_FILE_NAME

    with downloaded_file.open("a", encoding="utf-8") as file:
        file.write(
            f"{key}\t{file_path.stem}\t{author}\t{normalize_url(url)}\t"
            f"{format_like_count(like_count)}\n"
        )

    sync_state_file_to_drive(DOWNLOADED_FILE_NAME, category)


def get_next_video_number(category: dict) -> int:
    state_dir = get_state_dir(category)
    counter_file = state_dir / NEXT_NUMBER_FILE_NAME
    next_number = 1

    if not counter_file.exists():
        next_number = 1
    else:
        try:
            next_number = int(counter_file.read_text(encoding="utf-8").strip())
        except ValueError:
            logger.warning("Invalid counter file ignored: %s", counter_file)
            next_number = 1

    next_number = max(next_number, 1)

    try:
        folder_id = get_category_drive_folder_id(category)
        drive_numbers = get_drive_video_numbers(folder_id)
    except Exception:
        logger.warning(
            "Could not check Google Drive folder numbers for %s",
            get_category_label(category),
            exc_info=True,
        )
        drive_numbers = []

    if drive_numbers:
        next_number = max(next_number, max(drive_numbers) + 1)

    return next_number


def save_next_video_number(category: dict, next_number: int) -> None:
    state_dir = get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    counter_file = state_dir / NEXT_NUMBER_FILE_NAME
    counter_file.write_text(f"{next_number}\n", encoding="utf-8")
    sync_state_file_to_drive(NEXT_NUMBER_FILE_NAME, category)


def finish_downloaded_file(
    file_path: Path,
    video_key: str,
    video_number: int,
    author: str,
    author_url: str,
    url: str,
    like_count: int | None,
    category: dict,
) -> dict[str, str]:
    try:
        target_folder_id = get_category_drive_folder_id(category)
        drive_file_id = upload_to_drive(file_path, parent_id=target_folder_id)
        save_video_author(file_path, author, like_count, category)
        save_downloaded_video(video_key, file_path, author, url, like_count, category)
        save_next_video_number(category, video_number + 1)
        return {
            "drive_file_id": drive_file_id,
            "drive_file_url": get_drive_file_url(drive_file_id),
            "author": author,
            "author_url": author_url,
            "like_count": format_like_count(like_count),
            "category": get_category_label(category),
        }
    finally:
        delete_local_video_file(file_path)


def find_duplicate_by_url(url: str) -> dict[str, str] | None:
    normalized_url = normalize_url(url)
    for video in get_downloaded_videos().values():
        if normalize_url(video["url"]) == normalized_url:
            return video

    return None


def download_video(url: str) -> dict[str, str]:
    if not DRIVE_SERVICE:
        raise RuntimeError("Google Drive is not configured. Check OAuth variables in .env.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temp_video_dir = get_temp_video_dir()
    temp_video_dir.mkdir(parents=True, exist_ok=True)

    duplicate = find_duplicate_by_url(url)
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

    video_key = get_video_key(info, url)
    downloaded_videos = get_downloaded_videos()
    if video_key in downloaded_videos:
        saved_video = downloaded_videos[video_key]
        raise DuplicateVideoError(
            saved_video["number"],
            saved_video["author"],
            saved_video.get("category", ""),
        )

    like_count = get_like_count(info)
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

    like_count = get_like_count(info) or like_count
    category = get_like_category(like_count)
    video_number = get_next_video_number(category)
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
        file_path = ensure_video_file_name(new_files[0], video_number, like_count)
        return finish_downloaded_file(
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
        file_path = ensure_video_file_name(prepared, video_number, like_count)
        return finish_downloaded_file(
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
        file_path = ensure_video_file_name(mp4_file, video_number, like_count)
        return finish_downloaded_file(
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


async def process_video_url(message, url: str) -> None:
    await message.chat.send_action(ChatAction.TYPING)
    status = await message.reply_text("Завантажую відео на Google Drive...")
    retry_delays = get_retry_delays()

    for attempt in range(len(retry_delays) + 1):
        try:
            result = await asyncio.to_thread(download_video, url)
            break
        except DuplicateVideoError as exc:
            category_text = f"\nПапка: {exc.category}" if exc.category else ""
            await status.edit_text(
                "Це відео вже було завантажене.\n"
                f"Файл: {exc.video_number}.mp4\n"
                f"Автор: {exc.author}"
                f"{category_text}"
            )
            return
        except Exception as exc:
            if is_rate_limit_error(exc) and attempt < len(retry_delays):
                delay = retry_delays[attempt]
                logger.warning(
                    "TikTok rate limit. Retrying in %s seconds. url=%s",
                    delay,
                    url,
                )
                await status.edit_text(
                    "TikTok тимчасово обмежив запити.\n"
                    f"Спробую ще раз через {format_delay(delay)}.\n"
                    f"Спроба {attempt + 1}/{len(retry_delays)}"
                )
                await asyncio.sleep(delay)
                await message.chat.send_action(ChatAction.TYPING)
                await status.edit_text("Пробую завантажити відео ще раз...")
                continue

            logger.exception("Download failed")
            await status.edit_text(f"Не вдалося завантажити відео: {exc}")
            return

    author_text = result["author"]
    if result["author_url"]:
        author_text = f"{author_text}\n{result['author_url']}"

    await status.edit_text(
        "Готово. Відео завантажено на Google Drive:\n"
        f"{result['drive_file_url']}\n\n"
        f"Папка: {result['category']}\n"
        f"Google Drive file id: {result['drive_file_id']}\n"
        f"Лайки: {result['like_count']}\n"
        f"Автор: {author_text}"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    text = message.text or message.caption or ""
    logger.info(
        "Received message. chat_id=%s thread_id=%s content_type=%s text=%s",
        message.chat_id,
        message.message_thread_id,
        "text" if message.text else "caption" if message.caption else "other",
        text,
    )

    url = extract_url(text)
    if not url:
        return

    await process_video_url(message, url)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to the .env file.")
    if not DRIVE_SERVICE:
        raise RuntimeError(
            "Google Drive is not configured. Add GOOGLE_DRIVE_FOLDER_ID, "
            "GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and "
            "GOOGLE_OAUTH_REFRESH_TOKEN to .env."
        )

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    sync_state_from_drive()
    start_health_server()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.ALL, handle_message))

    logger.info("Bot started. Temporary download directory: %s", DOWNLOAD_DIR)
    logger.info("Google Drive upload: enabled")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
