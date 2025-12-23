import os
import json
from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_flow(request, state=None):
    client_config_str = os.environ.get("GOOGLE_OAUTH_JSON")
    if not client_config_str:
        raise ValueError("GOOGLE_OAUTH_JSON environment variable not set")
    
    try:
        client_config = json.loads(client_config_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in GOOGLE_OAUTH_JSON: {e}")
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state
    )
    
    full_url = str(request.url_for("oauth_callback"))
    if "run.app" in full_url:
        full_url = full_url.replace("http://", "https://")
    
    flow.redirect_uri = full_url
        
    return flow