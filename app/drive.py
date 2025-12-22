import io
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

def download_file_bytes(drive, file_id):
    meta = drive.files().get(
        fileId=file_id, fields="name,mimeType"
    ).execute()

    mime = meta["mimeType"]
    name = meta["name"]

    if mime in GOOGLE_EXPORTS:
        export_mime, ext = GOOGLE_EXPORTS[mime]
        req = drive.files().export_media(
            fileId=file_id, mimeType=export_mime
        )
        name += ext
    else:
        req = drive.files().get_media(fileId=file_id)

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    fh.seek(0)
    return name, fh.read()
