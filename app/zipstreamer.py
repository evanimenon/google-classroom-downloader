import zipstream
import logging
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

def stream_zip(generator):
    def zip_generator():
        z = zipstream.ZipFile(
            mode="w",
            compression=zipstream.ZIP_DEFLATED,
        )

        yield b""

        for path, data in generator:
            try:
                z.write_iter(path, [data])

                for chunk in z:
                    yield chunk

            except Exception as e:
                logger.exception(f"Failed to add file to zip: {path}")
                continue

    return StreamingResponse(
        zip_generator(),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=classroom_download.zip",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
