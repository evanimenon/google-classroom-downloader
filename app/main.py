import os
import json
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.oauth import get_flow, SCOPES
from app.classroom import list_all_courses, list_course_files
from app.drive import download_file_bytes
from app.zipstreamer import stream_zip

SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET environment variable not set")

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=True,
)

templates = Jinja2Templates(directory="app/templates")


@app.get("/")
def home():
    return RedirectResponse("/login")


@app.get("/login")
def login(request: Request):
    flow = get_flow(request)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    request.session["state"] = state
    return RedirectResponse(auth_url)


@app.get("/callback")
def oauth_callback(request: Request):
    if "state" not in request.session:
        return RedirectResponse("/login")

    flow = get_flow(request, request.session["state"])
    flow.fetch_token(authorization_response=str(request.url))
    request.session["token"] = json.loads(flow.credentials.to_json())
    return RedirectResponse("/courses")


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
            for fid, _ in files:
                name, data = download_file_bytes(drive, fid)
                yield f"{cid}/{name}", data

    return stream_zip(gen())
