import downloader.file_manager as fm
import config.settings as s
import drive.metadata as m
import storage.state as st
import drive.client as cl
import utils.likes as lk
import mimetypes
import io


from pathlib import Path
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload


def download_drive_text_file(name: str, category: dict) -> None:
    parent_id = m.get_category_drive_folder_id(category)
    file_id = m.find_drive_file_id(name, parent_id)
    if not cl.DRIVE_SERVICE or not file_id:
        return

    state_dir = fm.get_state_dir(category)
    state_dir.mkdir(parents=True, exist_ok=True)
    request = cl.DRIVE_SERVICE.files().get_media(fileId=file_id)
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
    if not cl.DRIVE_SERVICE:
        raise RuntimeError("Google Drive is not configured.")

    upload_name = name or file_path.name
    mime_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)

    if replace:
        file_id = m.find_drive_file_id(upload_name, parent_id)
        if file_id:
            result = (
                cl.DRIVE_SERVICE.files()
                .update(fileId=file_id, media_body=media, fields="id")
                .execute()
            )
            return result["id"]

    metadata = {"name": upload_name, "parents": [parent_id]}
    result = (
        cl.DRIVE_SERVICE.files()
        .create(body=metadata, media_body=media, fields="id")
        .execute()
    )
    return result["id"]


def sync_state_from_drive() -> None:
    for category in s.ALL_CATEGORIES:
        download_drive_text_file(s.AUTHORS_FILE_NAME, category)
        download_drive_text_file(s.DOWNLOADED_FILE_NAME, category)
        download_drive_text_file(s.NEXT_NUMBER_FILE_NAME, category)


def sync_state_file_to_drive(name: str, category: dict) -> None:
    state_dir = fm.get_state_dir(category)
    file_path = state_dir / name
    if file_path.exists():
        upload_to_drive(
            file_path,
            parent_id=m.get_category_drive_folder_id(category),
            name=name,
            replace=True,
        )

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
        target_folder_id = m.get_category_drive_folder_id(category)
        drive_file_id = upload_to_drive(file_path, parent_id=target_folder_id)
        print(f"drive_file_id: {drive_file_id}")
        st.save_video_author(file_path, author, like_count, category)
        st.save_downloaded_video(video_key, file_path, author, url, like_count, category)
        st.save_next_video_number(category, video_number + 1)
        return {
            "drive_file_id": drive_file_id,
            "drive_file_url": m.get_drive_file_url(drive_file_id),
            "author": author,
            "author_url": author_url,
            "like_count": lk.format_like_count(like_count),
            "category": lk.get_category_label(category),
        }
    finally:
        fm.delete_local_video_file(file_path)