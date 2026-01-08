import os
import io
import json
import pathlib
import re
import argparse
from typing import Dict, Set, List, Tuple

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ---- SCOPES ----
SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]



TOKEN_FILE = "token.json"
INDEX_FILE = "download_index.json"


# ---------- AUTH HELPERS ----------

def get_credentials() -> Credentials:
    """
    Load credentials from token.json, or start OAuth flow from credentials.json.
    """
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "credentials.json not found. "
                    "Download it from Google Cloud Console and place it next to this script."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds


# ---------- UTILS ----------

def safe_filename(name: str) -> str:
    """
    Make a filesystem-safe, not-too-long filename from an arbitrary string.
    """
    if not name:
        name = "unnamed"
    # Replace bad chars
    name = re.sub(r"[^\w.\- ]+", "_", name)
    # Strip and limit length
    name = name.strip()[:80]
    if not name:
        name = "file"
    return name


def load_index() -> Set[str]:
    """
    Load previously downloaded file IDs from INDEX_FILE.
    """
    if not os.path.exists(INDEX_FILE):
        return set()
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("downloaded_ids", []))
    except Exception:
        # If corrupted, ignore
        return set()


def save_index(downloaded_ids: Set[str]) -> None:
    """
    Save downloaded file IDs to INDEX_FILE.
    """
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({"downloaded_ids": sorted(downloaded_ids)}, f, indent=2)


# ---------- CLASSROOM HELPERS ----------

def list_all_courses(classroom_service, name_contains: str = None) -> List[Dict]:
    """
    List all courses the user is enrolled in. Optionally filter by name substring.
    """
    courses: List[Dict] = []
    page_token = None

    while True:
        resp = classroom_service.courses().list(
            pageToken=page_token
        ).execute()
        cs = resp.get("courses", [])
        for c in cs:
            if name_contains:
                if name_contains.lower() in c.get("name", "").lower():
                    courses.append(c)
            else:
                courses.append(c)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return courses


def list_course_files(classroom_service, course_id: str) -> List[Tuple[str, str]]:
    """
    For a given course, return list of (drive_file_id, name_hint).
    Combines CourseWork (assignments) and CourseWorkMaterials.
    """
    files: List[Tuple[str, str]] = []

    # ---- Coursework (assignments, quizzes etc.) ----
    cw_page_token = None
    while True:
        cw_resp = classroom_service.courses().courseWork().list(
            courseId=course_id,
            pageToken=cw_page_token
        ).execute()
        for cw in cw_resp.get("courseWork", []):
            title = cw.get("title", "Assignment")
            for mat in cw.get("materials", []):
                df = mat.get("driveFile")
                if df and "driveFile" in df:
                    inner = df["driveFile"]
                    file_id = inner.get("id")
                    if file_id:
                        files.append((file_id, inner.get("title", title)))
        cw_page_token = cw_resp.get("nextPageToken")
        if not cw_page_token:
            break

    # ---- Coursework materials (non-graded posts) ----
    m_page_token = None
    while True:
        m_resp = classroom_service.courses().courseWorkMaterials().list(
            courseId=course_id,
            pageToken=m_page_token
        ).execute()
        for m in m_resp.get("courseWorkMaterial", []):
            title = m.get("title", "Material")
            for mat in m.get("materials", []):
                df = mat.get("driveFile")
                if df and "driveFile" in df:
                    inner = df["driveFile"]
                    file_id = inner.get("id")
                    if file_id:
                        files.append((file_id, inner.get("title", title)))
        m_page_token = m_resp.get("nextPageToken")
        if not m_page_token:
            break

    return files


# ---------- DRIVE DOWNLOAD HELPERS ----------

GOOGLE_DOC_TYPES = {
    "application/vnd.google-apps.document": (  # Docs
        "application/pdf",
        ".pdf",
    ),
    "application/vnd.google-apps.presentation": (  # Slides
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.spreadsheet": (  # Sheets
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}


def download_drive_file(
    drive_service,
    file_id: str,
    dest_path: pathlib.Path,
    mime_type: str,
    dry_run: bool = False,
) -> None:
    """
    Download a Drive file to dest_path.
    Handles Google Docs / Sheets / Slides export via files.export.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        print(f"[DRY RUN] Would download: {file_id} -> {dest_path}")
        return

    if mime_type in GOOGLE_DOC_TYPES:
        export_mime, _ext = GOOGLE_DOC_TYPES[mime_type]
        request = drive_service.files().export_media(
            fileId=file_id, mimeType=export_mime
        )
    else:
        request = drive_service.files().get_media(fileId=file_id)

    fh = io.FileIO(dest_path, "wb")
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    print(f"Downloaded: {dest_path}")


def ensure_extension(name: str, mime_type: str) -> str:
    """
    For Google Docs/Slides/Sheets, add a sensible extension when exporting.
    For other mimetypes, keep the original name.
    """
    if mime_type in GOOGLE_DOC_TYPES:
        export_mime, ext = GOOGLE_DOC_TYPES[mime_type]
        if not name.lower().endswith(ext):
            name = name + ext
    return name


# ---------- CORE LOGIC ----------

def download_all_for_course(
    classroom_service,
    drive_service,
    course: Dict,
    base_dir: pathlib.Path,
    downloaded_ids: Set[str],
    dry_run: bool = False,
) -> None:
    course_name = course.get("name", f"course_{course.get('id')}")
    course_dir = base_dir / safe_filename(course_name)

    print(f"\n=== Course: {course_name} (id={course.get('id')}) ===")

    files = list_course_files(classroom_service, course["id"])
    print(f"Found {len(files)} attached Drive files in this course.")

    for file_id, name_hint in files:
        if file_id in downloaded_ids:
            # Already downloaded before
            continue

        # Get metadata from Drive
        meta = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType",
        ).execute()

        fname = meta.get("name") or name_hint or file_id
        mime_type = meta.get("mimeType")

        fname = safe_filename(fname)
        fname = ensure_extension(fname, mime_type)

        dest_path = course_dir / fname

        if dest_path.exists():
            print(f"File already exists locally, skipping: {dest_path}")
            downloaded_ids.add(file_id)
            continue

        download_drive_file(drive_service, file_id, dest_path, mime_type, dry_run=dry_run)
        downloaded_ids.add(file_id)


# ---------- MAIN / CLI ----------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Google Classroom auto-downloader (Classroom + Drive APIs)."
    )
    parser.add_argument(
        "--base-dir",
        default="downloads",
        help="Base directory where files will be saved (default: downloads)",
    )
    parser.add_argument(
        "--course-name-contains",
        default=None,
        help="If set, only download from courses whose name contains this substring (case-insensitive).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be downloaded, but do not actually download.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    base_dir = pathlib.Path(args.base_dir)

    print("Authenticating with Google...")
    creds = get_credentials()
    classroom_service = build("classroom", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    print("Loading download index...")
    downloaded_ids = load_index()

    print("Fetching courses...")
    courses = list_all_courses(classroom_service, args.course_name_contains)

    if not courses:
        print("No courses found that match your filter.")
        return

    print(f"Found {len(courses)} course(s).")

    for c in courses:
        download_all_for_course(
            classroom_service,
            drive_service,
            c,
            base_dir,
            downloaded_ids,
            dry_run=args.dry_run,
        )

    if not args.dry_run:
        print("\nSaving download index...")
        save_index(downloaded_ids)
        print("Done. All new files downloaded.")
    else:
        print("\nDry run completed. No files were actually downloaded.")


if __name__ == "__main__":
    main()
