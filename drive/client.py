import config.settings as s
from google.oauth2 import credentials as oauth_credentials
from googleapiclient.discovery import build

def get_drive_service():
    if not (
        s.GOOGLE_DRIVE_FOLDER_ID
        and s.GOOGLE_OAUTH_CLIENT_ID
        and s.GOOGLE_OAUTH_CLIENT_SECRET
        and s.GOOGLE_OAUTH_REFRESH_TOKEN
    ):
        return None

    credentials = oauth_credentials.Credentials(
        token=None,
        refresh_token=s.GOOGLE_OAUTH_REFRESH_TOKEN,
        token_uri=s.GOOGLE_TOKEN_URI,
        client_id=s.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=s.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=s.DRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)

DRIVE_SERVICE = get_drive_service()