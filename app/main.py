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
        raise RuntimeError("SESSION_SECRET not set")
    SESSION_SECRET = "dev-secret"

app = FastAPI()

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

IS_LOCAL = os.environ.get("K_SERVICE") is None

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="classroom_session_v5",
    same_site="none" if not IS_LOCAL else "lax",
    https_only=not IS_LOCAL,
)

templates = Jinja2Templates(directory="app/templates")


@app.get("/")
def home():
    return RedirectResponse("/login")


@app.get("/login")
def login(request: Request):
    request.session.clear()

    try:
        flow = get_flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )

        request.session["state"] = state

        return RedirectResponse(auth_url)

    except Exception:
        logger.exception("Login error")
        raise HTTPException(status_code=500, detail="Login failed")


@app.get("/callback")
def oauth_callback(request: Request):
    state = request.session.get("state")
    if not state:
        return RedirectResponse("/login")

    try:
        flow = get_flow(state=state)
        flow.fetch_token(authorization_response=str(request.url))

        request.session["token"] = json.loads(
            flow.credentials.to_json()
        )

        return RedirectResponse("/courses")

    except Exception:
        logger.exception("OAuth callback failed")
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
        "courses.html",
        {
            "request": request,
            "courses": courses,
        },
    )


# ======================= FIXED ENDPOINT =======================

@app.post("/download")
def download(
    request: Request,
    course_ids: list[str] = Form(None),
    file_ids: list[str] = Form(None),
):
    if "token" not in request.session:
        return RedirectResponse("/login")

    creds = Credentials.from_authorized_user_info(
        request.session["token"], SCOPES
    )

    classroom = build("classroom", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    def gen():
        if file_ids:
            logger.info(f"=== DOWNLOADING {len(file_ids)} SELECTED FILES ===")
            for fid in file_ids:
                name, data = download_file_bytes(drive, fid)
                if name and data:
                    yield f"files/{name}", data
        else:
            logger.info(f"=== STARTING DOWNLOAD FOR {len(course_ids)} COURSES ===")
            for cid in course_ids:
                logger.info(f"Processing Course: {cid}")
                files = list_course_files(classroom, cid)
                for fid, _ in files:
                    name, data = download_file_bytes(drive, fid)
                    if name and data:
                        yield f"{cid}/{name}", data
        logger.info("=== ZIP GENERATION COMPLETE ===")

    return stream_zip(gen())


# =============================================================


@app.get("/api/courses/{course_id}/files")
def get_course_files(request: Request, course_id: str):
    if "token" not in request.session:
        raise HTTPException(status_code=401)
    
    creds = Credentials.from_authorized_user_info(request.session["token"], SCOPES)
    classroom = build("classroom", "v1", credentials=creds)
    
    files = list_course_files(classroom, course_id)
    return [{"id": f[0], "name": f[1]} for f in files]
