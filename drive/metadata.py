import config.settings as s
import drive.client as cl

from pathlib import Path

DRIVE_FOLDER_CACHE: dict[str, str] = {}

def escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_drive_file_id(
    name: str,
    parent_id: str,
    mime_type: str | None = None,
) -> str | None:
    if not cl.DRIVE_SERVICE:
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
        cl.DRIVE_SERVICE.files()
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
    if not cl.DRIVE_SERVICE:
        return []

    numbers = []
    page_token = None
    query = f"'{parent_id}' in parents and trashed = false"

    while True:
        response = (
            cl.DRIVE_SERVICE.files()
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
            if Path(name).suffix.lower() not in s.VIDEO_FILE_SUFFIXES:
                continue

            match = s.VIDEO_NUMBER_RE.match(name)
            if match:
                numbers.append(int(match.group(1)))

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return numbers


def get_category_drive_folder_id(category: dict) -> str:
    if category["key"] == s.ROOT_CATEGORY["key"]:
        return s.GOOGLE_DRIVE_FOLDER_ID

    cache_key = str(category["key"])
    if cache_key in DRIVE_FOLDER_CACHE:
        return DRIVE_FOLDER_CACHE[cache_key]

    folder_name = str(category["folder_name"])
    folder_id = find_drive_file_id(
        folder_name,
        s.GOOGLE_DRIVE_FOLDER_ID,
        mime_type=s.DRIVE_FOLDER_MIME_TYPE,
    )
    if not folder_id:
        metadata = {
            "name": folder_name,
            "mimeType": s.DRIVE_FOLDER_MIME_TYPE,
            "parents": [s.GOOGLE_DRIVE_FOLDER_ID],
        }
        result = cl.DRIVE_SERVICE.files().create(body=metadata, fields="id").execute()
        folder_id = result["id"]

    DRIVE_FOLDER_CACHE[cache_key] = folder_id
    return folder_id

def get_drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"