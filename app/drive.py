import io
import re
from googleapiclient.http import MediaIoBaseDownload

GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "_", name or "file")
    return name.strip()[:80] or "file"


def download_file_bytes(drive, file_id: str):
    meta = drive.files().get(
        fileId=file_id,
        fields="name,mimeType",
    ).execute()

    name = safe_filename(meta.get("name"))
    mime = meta.get("mimeType")

    if mime in GOOGLE_EXPORTS:
        export_mime, ext = GOOGLE_EXPORTS[mime]
        request = drive.files().export_media(
            fileId=file_id,
            mimeType=export_mime,
        )
        if not name.lower().endswith(ext):
            name += ext
    else:
        request = drive.files().get_media(fileId=file_id)

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    fh.seek(0)
    return name, fh.read()
