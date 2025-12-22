def list_all_courses(classroom):
    courses = []
    page = None
    while True:
        resp = classroom.courses().list(
            pageToken=page,
            courseStates=["ACTIVE", "ARCHIVED"]
        ).execute()
        courses.extend(resp.get("courses", []))
        page = resp.get("nextPageToken")
        if not page:
            break
    return courses


def list_course_files(classroom, course_id):
    files = []

    cw_page = None
    while True:
        resp = classroom.courses().courseWork().list(
            courseId=course_id, pageToken=cw_page
        ).execute()
        for cw in resp.get("courseWork", []):
            for m in cw.get("materials", []):
                df = m.get("driveFile", {}).get("driveFile")
                if df:
                    files.append((df["id"], df.get("title", "file")))
        cw_page = resp.get("nextPageToken")
        if not cw_page:
            break

    m_page = None
    while True:
        resp = classroom.courses().courseWorkMaterials().list(
            courseId=course_id, pageToken=m_page
        ).execute()
        for mat in resp.get("courseWorkMaterial", []):
            for m in mat.get("materials", []):
                df = m.get("driveFile", {}).get("driveFile")
                if df:
                    files.append((df["id"], df.get("title", "file")))
        m_page = resp.get("nextPageToken")
        if not m_page:
            break

    return files
