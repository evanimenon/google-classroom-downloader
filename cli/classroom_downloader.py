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
from googleapiclient.errors import HttpError


# ============================================================
# SCOPES
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

TOKEN_FILE = "token.json"
INDEX_FILE = "download_index.json"


# ============================================================
# AUTHENTICATION
# ============================================================

def get_credentials():
    """
    Load credentials from token.json.
    If unavailable or invalid, authenticate using credentials.json.
    """

    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(
                TOKEN_FILE,
                SCOPES
            )
        except Exception:
            creds = None

    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:

            creds.refresh(Request())

        else:

            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "credentials.json not found. "
                    "Place it in the same folder as this script."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES
            )

            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def safe_filename(name):
    """
    Convert a filename into a Windows-safe filename.
    """

    if not name:
        name = "unnamed"

    name = re.sub(r'[<>:"/\\|?*]+', "_", name)

    name = name.strip()

    if not name:
        name = "file"

    # Avoid excessively long Windows paths
    return name[:120]


def load_index():
    """
    Load IDs of successfully downloaded files.
    """

    if not os.path.exists(INDEX_FILE):
        return set()

    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return set(data.get("downloaded_ids", []))

    except Exception:
        return set()


def save_index(downloaded_ids):
    """
    Save IDs of successfully downloaded files.
    """

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "downloaded_ids": sorted(downloaded_ids)
            },
            f,
            indent=2
        )


# ============================================================
# LIST COURSES
# ============================================================

def list_all_courses(classroom_service, name_contains=None):

    courses = []

    page_token = None

    while True:

        response = classroom_service.courses().list(
            pageToken=page_token
        ).execute()

        for course in response.get("courses", []):

            course_name = course.get("name", "")

            if name_contains:

                if name_contains.lower() in course_name.lower():
                    courses.append(course)

            else:
                courses.append(course)

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return courses


# ============================================================
# EXTRACT DRIVE FILES FROM CLASSROOM MATERIALS
# ============================================================

def extract_drive_files(materials, default_title):

    """
    Extract Drive file IDs from a Classroom materials list.

    Returns:
        List of tuples:
        (file_id, title_hint)
    """

    files = []

    for material in materials or []:

        drive_file_wrapper = material.get("driveFile")

        if not drive_file_wrapper:
            continue

        drive_file = drive_file_wrapper.get("driveFile", {})

        file_id = drive_file.get("id")

        if not file_id:
            continue

        title_hint = (
            drive_file.get("title")
            or default_title
            or "unnamed"
        )

        files.append(
            (
                file_id,
                title_hint
            )
        )

    return files


# ============================================================
# SCAN ALL CLASSROOM LOCATIONS
# ============================================================

def list_course_files(classroom_service, course_id):

    """
    Scan ALL relevant Classroom locations:

    1. Coursework / Assignments
    2. Coursework Materials
    3. Announcements / Stream

    All pages are scanned.
    """

    files = []
    seen_ids = set()

    # --------------------------------------------------------
    # 1. COURSEWORK / ASSIGNMENTS
    # --------------------------------------------------------

    print("  Scanning coursework...")

    page_token = None

    while True:

        response = (
            classroom_service
            .courses()
            .courseWork()
            .list(
                courseId=course_id,
                pageToken=page_token
            )
            .execute()
        )

        for coursework in response.get("courseWork", []):

            title = coursework.get(
                "title",
                "Assignment"
            )

            found_files = extract_drive_files(
                coursework.get("materials", []),
                title
            )

            for file_id, title_hint in found_files:

                if file_id not in seen_ids:

                    seen_ids.add(file_id)

                    files.append(
                        (
                            file_id,
                            title_hint
                        )
                    )

        page_token = response.get("nextPageToken")

        if not page_token:
            break


    # --------------------------------------------------------
    # 2. COURSEWORK MATERIALS
    # --------------------------------------------------------

    print("  Scanning coursework materials...")

    page_token = None

    while True:

        response = (
            classroom_service
            .courses()
            .courseWorkMaterials()
            .list(
                courseId=course_id,
                pageToken=page_token
            )
            .execute()
        )

        for material_post in response.get(
            "courseWorkMaterial",
            []
        ):

            title = material_post.get(
                "title",
                "Material"
            )

            found_files = extract_drive_files(
                material_post.get("materials", []),
                title
            )

            for file_id, title_hint in found_files:

                if file_id not in seen_ids:

                    seen_ids.add(file_id)

                    files.append(
                        (
                            file_id,
                            title_hint
                        )
                    )

        page_token = response.get("nextPageToken")

        if not page_token:
            break


    # --------------------------------------------------------
    # 3. ANNOUNCEMENTS / STREAM
    # --------------------------------------------------------

    print("  Scanning ALL Stream announcements...")

    page_token = None

    total_announcements = 0
    announcement_files = 0

    while True:

        response = (
            classroom_service
            .courses()
            .announcements()
            .list(
                courseId=course_id,
                pageToken=page_token
            )
            .execute()
        )

        announcements = response.get(
            "announcements",
            []
        )

        total_announcements += len(announcements)

        for announcement in announcements:

            announcement_title = (
                announcement.get("text")
                or "Stream Announcement"
            )

            found_files = extract_drive_files(
                announcement.get("materials", []),
                announcement_title
            )

            for file_id, title_hint in found_files:

                if file_id not in seen_ids:

                    seen_ids.add(file_id)

                    files.append(
                        (
                            file_id,
                            title_hint
                        )
                    )

                    announcement_files += 1

        page_token = response.get("nextPageToken")

        if not page_token:
            break

    print(
        f"  Scanned {total_announcements} Stream announcement(s)."
    )

    print(
        f"  Found {announcement_files} new Drive file(s) "
        f"from Stream announcements."
    )

    return files


# ============================================================
# GOOGLE FILE EXPORT TYPES
# ============================================================

GOOGLE_DOC_TYPES = {

    "application/vnd.google-apps.document": (
        "application/pdf",
        ".pdf"
    ),

    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx"
    ),

    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx"
    ),
}


def ensure_extension(filename, mime_type):

    """
    Add correct extension for exported Google-native files.
    """

    if mime_type in GOOGLE_DOC_TYPES:

        _, extension = GOOGLE_DOC_TYPES[mime_type]

        if not filename.lower().endswith(extension):
            filename += extension

    return filename


# ============================================================
# DOWNLOAD ONE DRIVE FILE
# ============================================================

def download_drive_file(
    drive_service,
    file_id,
    destination,
    mime_type,
    dry_run=False
):

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if dry_run:

        print(
            f"[DRY RUN] Would download: "
            f"{destination}"
        )

        return


    # --------------------------------------------------------
    # Google Docs / Slides / Sheets
    # --------------------------------------------------------

    if mime_type in GOOGLE_DOC_TYPES:

        export_mime, _ = GOOGLE_DOC_TYPES[mime_type]

        request = drive_service.files().export_media(
            fileId=file_id,
            mimeType=export_mime
        )

    # --------------------------------------------------------
    # Normal files: PDF, PPTX, DOCX, ZIP, images, etc.
    # --------------------------------------------------------

    else:

        request = drive_service.files().get_media(
            fileId=file_id
        )


    # --------------------------------------------------------
    # Download
    # --------------------------------------------------------

    with io.FileIO(destination, "wb") as fh:

        downloader = MediaIoBaseDownload(
            fh,
            request
        )

        done = False

        while not done:

            _, done = downloader.next_chunk()


# ============================================================
# DOWNLOAD ALL FILES FOR ONE COURSE
# ============================================================

def download_all_for_course(
    classroom_service,
    drive_service,
    course,
    base_dir,
    downloaded_ids,
    dry_run=False
):

    course_name = course.get(
        "name",
        f"course_{course.get('id')}"
    )

    course_dir = (
        base_dir
        / safe_filename(course_name)
    )

    print()

    print(
        f"=== Course: {course_name} "
        f"(id={course.get('id')}) ==="
    )


    # Scan assignments + materials + ALL announcements
    files = list_course_files(
        classroom_service,
        course["id"]
    )

    print(
        f"Found {len(files)} unique attached Drive file(s) "
        f"in this course."
    )


    for file_id, title_hint in files:

        # ----------------------------------------------------
        # Already successfully downloaded earlier
        # ----------------------------------------------------

        if file_id in downloaded_ids:

            print(
                f"Already downloaded earlier, skipping ID: "
                f"{file_id}"
            )

            continue


        # ----------------------------------------------------
        # Get file metadata
        # ----------------------------------------------------

        try:

            meta = drive_service.files().get(
                fileId=file_id,
                fields="name,mimeType"
            ).execute()


        except HttpError as e:

            status = e.resp.status

            print()

            print(
                f"SKIPPED - Cannot access metadata"
            )

            print(
                f"File ID: {file_id}"
            )

            print(
                f"Title hint: {title_hint}"
            )

            print(
                f"Google Drive error: HTTP {status}"
            )

            # Continue with the next file
            continue


        # ----------------------------------------------------
        # Determine actual filename
        # ----------------------------------------------------

        filename = (
            meta.get("name")
            or title_hint
            or file_id
        )

        mime_type = meta.get("mimeType")

        filename = safe_filename(filename)

        filename = ensure_extension(
            filename,
            mime_type
        )

        destination = course_dir / filename


        # ----------------------------------------------------
        # Print EXACT FILE NAME BEFORE DOWNLOADING
        # ----------------------------------------------------

        print()

        print(f"Downloading: {filename}")

        print(f"File ID: {file_id}")

        print(f"Course: {course_name}")


        # ----------------------------------------------------
        # Skip if same destination already exists
        # ----------------------------------------------------

        if destination.exists():

            print(
                "File already exists locally. Skipping."
            )

            downloaded_ids.add(file_id)

            if not dry_run:
                save_index(downloaded_ids)

            continue


        # ----------------------------------------------------
        # Try downloading
        # ----------------------------------------------------

        try:

            download_drive_file(
                drive_service,
                file_id,
                destination,
                mime_type,
                dry_run=dry_run
            )


            # ------------------------------------------------
            # Only mark successful downloads
            # ------------------------------------------------

            downloaded_ids.add(file_id)

            if not dry_run:

                save_index(downloaded_ids)

            print(
                f"SUCCESS: {filename}"
            )


        except HttpError as e:

            status = e.resp.status

            print()

            print(
                f"SKIPPED: {filename}"
            )

            print(
                f"File ID: {file_id}"
            )

            print(
                f"Google blocked this download."
            )

            print(
                f"HTTP Status: {status}"
            )

            # Important:
            # DO NOT add this ID to downloaded_ids.
            # If permissions change later, the script can
            # try this file again.

            continue


        except Exception as e:

            print()

            print(
                f"ERROR downloading: {filename}"
            )

            print(
                f"File ID: {file_id}"
            )

            print(
                f"Error: {e}"
            )

            # Continue with remaining files
            continue


# ============================================================
# COMMAND LINE ARGUMENTS
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "Download Google Classroom Drive attachments "
            "from Coursework, Materials and ALL Stream Announcements."
        )
    )


    parser.add_argument(
        "--base-dir",
        default="downloads",
        help=(
            "Directory where downloads are saved. "
            "Default: downloads"
        )
    )


    parser.add_argument(
        "--course-name-contains",
        default=None,
        help=(
            "Only process courses whose names contain this text."
        )
    )


    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Scan all files without downloading."
        )
    )


    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main():

    args = parse_args()

    base_dir = pathlib.Path(
        args.base_dir
    )

    print(
        "Authenticating with Google..."
    )


    # --------------------------------------------------------
    # Authenticate
    # --------------------------------------------------------

    creds = get_credentials()


    # --------------------------------------------------------
    # Create Classroom API client
    # --------------------------------------------------------

    classroom_service = build(
        "classroom",
        "v1",
        credentials=creds
    )


    # --------------------------------------------------------
    # Create Drive API client
    # --------------------------------------------------------

    drive_service = build(
        "drive",
        "v3",
        credentials=creds
    )


    # --------------------------------------------------------
    # Load successful download history
    # --------------------------------------------------------

    print(
        "Loading download index..."
    )

    downloaded_ids = load_index()


    # --------------------------------------------------------
    # Fetch courses
    # --------------------------------------------------------

    print(
        "Fetching courses..."
    )

    courses = list_all_courses(
        classroom_service,
        args.course_name_contains
    )


    if not courses:

        print(
            "No courses found."
        )

        return


    print(
        f"Found {len(courses)} course(s)."
    )


    # --------------------------------------------------------
    # Process every course
    # --------------------------------------------------------

    for course in courses:

        download_all_for_course(
            classroom_service,
            drive_service,
            course,
            base_dir,
            downloaded_ids,
            dry_run=args.dry_run
        )


    # --------------------------------------------------------
    # Final save
    # --------------------------------------------------------

    if not args.dry_run:

        save_index(downloaded_ids)

        print()

        print("=" * 60)

        print(
            "DONE!"
        )

        print(
            "All courses were scanned."
        )

        print(
            "Successfully downloadable files were saved."
        )

        print(
            "Files blocked by Google were skipped without "
            "stopping the program."
        )

        print(
            f"Downloads location: "
            f"{base_dir.resolve()}"
        )

        print("=" * 60)


    else:

        print()

        print(
            "DRY RUN COMPLETE."
        )

        print(
            "No files were downloaded."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()


