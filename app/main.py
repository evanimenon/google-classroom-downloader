import os
import json
import logging
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.oauth import get_flow, SCOPES
from app.classroom import list_all_courses, list_course_files
from app.drive import download_file_bytes
from app.zipstreamer import stream_zip

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SESSION_SECRET = os.environ.get("SESSION_SECRET")
IS_CLOUD_RUN = os.environ.get("K_SERVICE") is not None

if not SESSION_SECRET:
    if IS_CLOUD_RUN:
        raise RuntimeError("SESSION_SECRET environment variable not set")
    else:
        SESSION_SECRET = "dev-secret-only-for-local"

app = FastAPI()

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="google_classroom_session",
    same_site="lax",
    https_only=False,
)

templates = Jinja2Templates(directory="app/templates")

@app.get("/")
def home():
    return RedirectResponse("/login")

@app.get("/login")
def login(request: Request):
    if IS_CLOUD_RUN:
        request.scope["scheme"] = "https"
    try:
        flow = get_flow(request)
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        request.session["state"] = state
        return RedirectResponse(auth_url)
    except Exception as e:
        logger.error(f"Login Init Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/callback")
def oauth_callback(request: Request):
    if "state" not in request.session:
        logger.error("Session state missing in callback")
        return RedirectResponse("/login")

    if IS_CLOUD_RUN:
        request.scope["scheme"] = "https"

    try:
        flow = get_flow(request, request.session["state"])
        flow.fetch_token(authorization_response=str(request.url))
        request.session["token"] = json.loads(flow.credentials.to_json())
        return RedirectResponse("/courses")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        return RedirectResponse("/login")

@app.get("/courses")
def courses(request: Request):
    if "token" not in request.session:
        return RedirectResponse("/login")

    creds = Credentials.from_authorized_user_info(
        request.session["token"], SCOPES
    )
    classroom = build("classroom", "v1", credentials=creds)
    courses = list_all_courses(classroom)

    return templates.TemplateResponse(
        "courses.html", {"request": request, "courses": courses}
    )

@app.post("/download")
def download(request: Request, course_ids: list[str] = Form(...)):
    if "token" not in request.session:
        return RedirectResponse("/login")

    creds = Credentials.from_authorized_user_info(
        request.session["token"], SCOPES
    )
    classroom = build("classroom", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    def gen():
        for cid in course_ids:
            files = list_course_files(classroom, cid)
            for fid, name in files:
                file_name, data = download_file_bytes(drive, fid)
                yield f"{cid}/{file_name}", data

    return stream_zip(gen())