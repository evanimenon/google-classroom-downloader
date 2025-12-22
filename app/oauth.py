import os
import json
from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

def get_flow(request, state=None):
    client_config_str = os.environ.get("GOOGLE_OAUTH_JSON")
    if not client_config_str:
        raise ValueError("GOOGLE_OAUTH_JSON environment variable not set")
    
    client_config = json.loads(client_config_str)
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state
    )
    

    flow.redirect_uri = str(request.url_for("oauth_callback"))
        
    return flow