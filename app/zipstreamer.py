import zipstream
from fastapi.responses import StreamingResponse

def stream_zip(generator):
    z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)
    for path, data in generator:
        z.write_iter(path, [data])

    return StreamingResponse(
        z,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=classroom_download.zip"
        },
    )
