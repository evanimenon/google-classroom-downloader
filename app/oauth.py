import os
import json
from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

REDIRECT_URI = "https://classroom-downloader-web-936773451602.asia-south1.run.app/callback"


def get_flow(state=None):
    client_config_str = os.environ.get("GOOGLE_OAUTH_JSON")
    if not client_config_str:
        raise RuntimeError("GOOGLE_OAUTH_JSON environment variable not set")

    client_config = json.loads(client_config_str)

    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )
