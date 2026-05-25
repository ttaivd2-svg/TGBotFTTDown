import bot


def main() -> None:
    folder_id = bot.GOOGLE_DRIVE_FOLDER_ID
    print("drive_enabled=", bool(bot.DRIVE_SERVICE))
    print("configured_folder_id=", folder_id)

    try:
        folder = (
            bot.DRIVE_SERVICE.files()
            .get(fileId=folder_id, fields="id,name,mimeType,trashed")
            .execute()
        )
        print("configured_folder=", folder)
    except Exception as exc:
        print("configured_folder_error=", exc)

    query = "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    response = (
        bot.DRIVE_SERVICE.files()
        .list(q=query, fields="files(id,name)", pageSize=50)
        .execute()
    )

    print("visible_folders=")
    for folder in response.get("files", []):
        print(f"{folder['id']}  {folder['name']}")


if __name__ == "__main__":
    main()
