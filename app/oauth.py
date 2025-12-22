import json
from google_auth_oauthlib.flow import Flow
from fastapi import Request

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

import os

CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_FILE", "credentials.json")

def get_flow(request: Request, state=None):
    redirect_uri = request.url_for("oauth_callback")
    print("REDIRECT URI USED:", redirect_uri)

    return Flow.from_client_secrets_file(
        CLIENT_SECRET,
        scopes=SCOPES,
        state=state,
        redirect_uri=redirect_uri,
    )


