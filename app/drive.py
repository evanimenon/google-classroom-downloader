import io
import re
import logging
from googleapiclient.http import MediaIoBaseDownload

logger = logging.getLogger(__name__)

GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
    "application/vnd.google-apps.jam": ("application/pdf", ".pdf"),
}

def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "_", name or "file")
    return name.strip()[:80] or "file"

def download_file_bytes(drive, file_id: str):
    try:
        meta = drive.files().get(fileId=file_id, fields="name,mimeType").execute()
        name = safe_filename(meta.get("name"))
        mime = meta.get("mimeType")

        logger.info(f"FETCHING: {name} ({mime})")

        if mime in GOOGLE_EXPORTS:
            export_mime, ext = GOOGLE_EXPORTS[mime]
            request = drive.files().export_media(fileId=file_id, mimeType=export_mime)
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
        logger.info(f"SUCCESS: {name}")
        return name, fh.read()
    except Exception as e:
        logger.error(f"SKIPPED: ID {file_id} failed. Error: {e}")
        return None, None