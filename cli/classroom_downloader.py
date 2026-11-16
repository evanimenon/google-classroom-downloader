#!/usr/bin/env python3
"""
Google Classroom Downloader — interactive CLI.

Run it with no arguments for the guided, menu-driven experience:

    python classroom_downloader.py

Or script it:

    python classroom_downloader.py --all --yes
    python classroom_downloader.py --course "CSE201" --layout flat --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

try:
    import questionary
    from questionary import Choice, Separator
    from rich import box
    from rich.console import Console, Group
    from rich.markup import escape
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TransferSpeedColumn,
    )
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme

    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload
except ImportError as _e:  # pragma: no cover - friendly message for new users
    print(
        f"\nMissing dependency: {_e.name}\n\n"
        "Install everything with:\n\n"
        f"    {sys.executable} -m pip install -r requirements.txt\n"
    )
    sys.exit(1)


# ───────────────────────────── constants ─────────────────────────────

APP_NAME = "Classroom Downloader"
VERSION = "2.0.0"

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.topics.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("GCD_HOME", Path.home() / ".classroom-downloader"))
CLIENT_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"
PREFS_FILE = CONFIG_DIR / "preferences.json"
INDEX_NAME = ".classroom-downloader-index.json"

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
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
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}
DRIVE_FIELDS = "id,name,mimeType,size,modifiedTime,webViewLink,shortcutDetails"

LAYOUTS = {
    "topic": "By topic — mirrors the Classwork page  (Course / Topic / file)",
    "post": "By topic and post  (Course / Topic / Post title / file)",
    "flat": "Flat — everything straight in the course folder",
}

SOURCES = {
    "materials": "Classwork materials",
    "assignments": "Assignment attachments",
    "stream": "Files posted on the Stream (announcements)",
    "submissions": "My own submitted work",
}

# ───────────────────────────── look & feel ─────────────────────────────

THEME = Theme(
    {
        "accent": "bold #34a853",
        "accent2": "#fbbc04",
        "muted": "grey58",
        "ok": "green",
        "warn": "yellow",
        "err": "bold red",
        "link": "underline #8ab4f8",
    }
)
console = Console(theme=THEME, highlight=False)

Q_STYLE = questionary.Style(
    [
        ("qmark", "fg:#34a853 bold"),
        ("question", "bold"),
        ("answer", "fg:#fbbc04 bold"),
        ("pointer", "fg:#34a853 bold"),
        ("highlighted", "fg:#34a853 bold"),
        ("selected", "fg:#fbbc04"),
        ("separator", "fg:#6c6c6c"),
        ("instruction", "fg:#8a8a8a italic"),
        ("disabled", "fg:#6c6c6c italic"),
        ("muted", "fg:#8a8a8a"),
    ]
)


def banner() -> None:
    title = Text("  Google Classroom Downloader  ", style="bold black on #34a853")
    sub = Text("Every file from your classes, sorted and saved to your computer.", style="muted")
    console.print()
    console.print(Panel(Group(Text(" "), title, Text(" "), sub), box=box.ROUNDED,
                        border_style="#34a853", padding=(0, 2), expand=False,
                        subtitle=f"[muted]v{VERSION}[/]", subtitle_align="right"))


def info(msg: str) -> None:
    console.print(f"[accent]›[/] {msg}")


def warn(msg: str) -> None:
    console.print(f"[warn]![/] {msg}")


def error(msg: str) -> None:
    console.print(f"[err]✗[/] {msg}")


def ask(question):
    """Run a questionary prompt; exit cleanly on Ctrl-C / Esc."""
    answer = question.ask()
    if answer is None:
        console.print("\n[muted]Bye![/]")
        sys.exit(0)
    return answer


def select(message: str, choices, default=None, instruction: Optional[str] = None):
    return ask(questionary.select(message, choices=choices, default=default, style=Q_STYLE,
                                  instruction=instruction or "(use ↑ ↓ and Enter)"))


def confirm(message: str, default: bool = True) -> bool:
    return ask(questionary.confirm(message, default=default, style=Q_STYLE))


def open_path(path: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        elif platform.system() == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        info(f"Your files are in: {path}")


# ───────────────────────────── prefs / small utils ─────────────────────────────

def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def secure_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_name(name: str, max_len: int = 120) -> str:
    """Filesystem-safe name that keeps spaces, brackets, unicode etc."""
    name = _BAD_CHARS.sub("_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "untitled"
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            name = stem[: max_len - len(ext) - 1].rstrip(" .") + "." + ext
        else:
            name = name[:max_len].rstrip(" .")
    return name


def human_size(n: Optional[int]) -> str:
    if not n:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def http_reason(e: Exception) -> str:
    if isinstance(e, HttpError):
        try:
            details = json.loads(e.content.decode("utf-8"))["error"]
            return details.get("message") or str(e.resp.status)
        except Exception:
            return getattr(e, "reason", None) or f"HTTP {e.resp.status}"
    return str(e)


def http_status(e: Exception) -> Optional[int]:
    return int(e.resp.status) if isinstance(e, HttpError) else None


# ───────────────────────────── setup wizard ─────────────────────────────

SETUP_STEPS = [
    (
        "Create a (free) Google Cloud project",
        "https://console.cloud.google.com/projectcreate",
        [
            "Sign in with any Google account — it does not have to be your school one.",
            "Project name: anything, e.g. [b]Classroom Downloader[/b]. Click [b]Create[/b].",
            "Check the project picker at the top shows your new project before moving on.",
        ],
    ),
    (
        "Turn on the Google Classroom API",
        "https://console.cloud.google.com/apis/library/classroom.googleapis.com",
        ["Click the blue [b]Enable[/b] button."],
    ),
    (
        "Turn on the Google Drive API",
        "https://console.cloud.google.com/apis/library/drive.googleapis.com",
        ["Click the blue [b]Enable[/b] button."],
    ),
    (
        "Set up the login screen",
        "https://console.cloud.google.com/auth/overview",
        [
            "Click [b]Get started[/b].",
            "App name: [b]Classroom Downloader[/b]. Support email: pick yours.",
            "Audience: [b]Internal[/b] if you see it (school accounts), otherwise [b]External[/b].",
            "Contact email: yours → tick the agreement → [b]Create[/b].",
        ],
    ),
    (
        "Allow your Classroom account to log in",
        "https://console.cloud.google.com/auth/audience",
        [
            "[muted]Skip this step if you chose Internal.[/]",
            "Under [b]Test users[/b] click [b]+ Add users[/b], type the email you use for "
            "Google Classroom, then [b]Save[/b].",
            "[muted]Tip: test logins expire every 7 days. Clicking [b]Publish app[/b] on this page "
            "stops that (you'll just see an 'unverified app' notice when logging in).[/]",
        ],
    ),
    (
        "Create your desktop login key",
        "https://console.cloud.google.com/auth/clients/create",
        [
            "Application type: [b]Desktop app[/b]. Name: anything. Click [b]Create[/b].",
            "In the popup click [b]Download JSON[/b]. Save it anywhere — I'll find it in Downloads.",
        ],
    ),
]


def _validate_client_file(path: Path) -> Optional[str]:
    data = load_json(path, None)
    if not isinstance(data, dict):
        return "That file isn't valid JSON."
    if "installed" in data:
        return None
    if "web" in data:
        return ("That key is a 'Web application' key. Create one with application type "
                "'Desktop app' instead.")
    return "That doesn't look like a Google OAuth client file."


def _find_downloaded_client_file() -> Optional[Path]:
    candidates: List[Path] = []
    for d in (Path.home() / "Downloads", Path.home() / "Desktop", Path.cwd()):
        if d.is_dir():
            candidates += list(d.glob("client_secret*.json")) + list(d.glob("credentials*.json"))
    candidates = [c for c in candidates if _validate_client_file(c) is None]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def _import_client_file(path: Path) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    secure_write(CLIENT_FILE, path.read_text(encoding="utf-8"))


def _ask_for_client_file() -> Path:
    while True:
        found = _find_downloaded_client_file()
        if found:
            age = time.time() - found.stat().st_mtime
            when = f"{int(age // 60)} min ago" if age < 3600 else datetime.fromtimestamp(
                found.stat().st_mtime).strftime("%d %b %H:%M")
            console.print(f"\n[ok]✓[/] Found [b]{escape(found.name)}[/] in "
                          f"{escape(str(found.parent))} [muted](saved {when})[/]")
            choice = select("Use this file?", ["Yes, use it", "Choose a different file",
                                               "Look again"])
            if choice == "Yes, use it":
                return found
            if choice == "Look again":
                continue
        else:
            console.print("\n[warn]I couldn't find the downloaded key in Downloads/Desktop.[/]")
            choice = select("What now?", ["Look again", "Type / drag in the file path"])
            if choice == "Look again":
                continue

        raw = ask(questionary.path("Path to the JSON file (you can drag it into this window):",
                                   style=Q_STYLE))
        p = Path(raw.strip().strip("'\"").replace("\\ ", " ")).expanduser()
        problem = "File not found." if not p.is_file() else _validate_client_file(p)
        if problem:
            error(problem)
            continue
        return p


def run_setup_wizard() -> None:
    console.print()
    console.print(Panel(
        "Google only lets apps it has reviewed log in for everyone, so for now each person "
        "makes their own free login key. It takes about [b]3 minutes[/] and you only do it "
        "[b]once[/].\n\n"
        "I'll walk you through it step by step and open each page for you.",
        title="[accent]One-time setup[/]", border_style="#34a853", box=box.ROUNDED, padding=(1, 2)))

    existing = _find_downloaded_client_file()
    if existing and confirm(f"Found an existing key ({existing.name}). Skip straight to using it?"):
        _import_client_file(existing)
        console.print("[ok]✓ Login key saved.[/]")
        return

    i = 0
    opened = set()
    while i < len(SETUP_STEPS):
        title, url, lines = SETUP_STEPS[i]
        body = "\n".join(f"  • {l}" for l in lines) + f"\n\n  [link]{url}[/]"
        console.print()
        console.print(Panel(body, title=f"[accent]Step {i + 1} of {len(SETUP_STEPS)}[/] · [bold white]{title}[/]",
                            title_align="left", border_style="grey35", box=box.ROUNDED,
                            padding=(1, 1)))
        choices = ["🌐  Open this page in my browser", "✅  Done — next step"]
        if i > 0:
            choices.append("⬅   Back")
        choices.append("✖   Quit setup")
        default = choices[1] if i in opened else choices[0]
        choice = select("Next:", choices, default=default)
        if choice.startswith("🌐"):
            webbrowser.open(url)
            opened.add(i)
            console.print("[muted]  Opened. Come back here when you're done.[/]")
        elif choice.startswith("✅"):
            i += 1
        elif choice.startswith("⬅"):
            i -= 1
        else:
            console.print("[muted]Setup cancelled. Run me again any time to continue.[/]")
            sys.exit(0)

    path = _ask_for_client_file()
    _import_client_file(path)
    console.print("[ok]✓ Login key saved[/] [muted](a private copy lives in "
                  f"{escape(str(CONFIG_DIR))} — you can delete the download).[/]")


# ───────────────────────────── auth ─────────────────────────────

def ensure_client_file() -> None:
    """Make sure we have an OAuth client file, importing legacy locations or running setup."""
    if CLIENT_FILE.exists() and _validate_client_file(CLIENT_FILE) is None:
        return
    env = os.environ.get("GCD_CREDENTIALS")
    for legacy in filter(None, [env, SCRIPT_DIR / "credentials.json", Path.cwd() / "credentials.json"]):
        legacy = Path(legacy)
        if legacy.is_file() and _validate_client_file(legacy) is None:
            _import_client_file(legacy)
            return
    if not sys.stdin.isatty():
        error("No login key found. Run this script once in a terminal to do the setup.")
        sys.exit(1)
    run_setup_wizard()


def browser_login() -> Credentials:
    console.print()
    console.print(Panel(
        "A browser tab will open so you can sign in with your [b]Google Classroom account[/].\n\n"
        "[muted]• If Google says [i]“Google hasn't verified this app”[/i], that's expected. "
        "Click [b]Continue[/b] (or [b]Advanced → Go to …[/b]).\n"
        "• Tick all the permission boxes so the downloader can read your classes and files.[/]",
        title="[accent]Sign in[/]", border_style="#34a853", box=box.ROUNDED, padding=(1, 2)))
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    try:
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            prompt="select_account consent",
            authorization_prompt_message="[If the browser didn't open, paste this link into it]\n{url}\n",
            success_message="Signed in! You can close this tab and go back to the terminal.",
        )
    except Exception as e:  # access_denied, blocked app, closed tab, ...
        error(f"Sign-in didn't complete: {e}")
        console.print(
            "[muted]Common fixes:\n"
            "  • 'Access blocked / app not verified': add your email as a Test user (setup step 5).\n"
            "  • 'Admin has blocked this app': your school blocks unapproved apps — try the setup\n"
            "    again choosing [b]Internal[/b], or ask your IT admin.\n"
            "  • Wrong account? Run again and choose 'Switch Google account'.[/]")
        sys.exit(1)
    secure_write(TOKEN_FILE, creds.to_json())
    return creds


def get_credentials(force_login: bool = False) -> Credentials:
    ensure_client_file()
    creds: Optional[Credentials] = None
    if TOKEN_FILE.exists() and not force_login:
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
        except Exception:
            creds = None
        if creds and not creds.has_scopes(SCOPES):
            info("This version needs a couple of new permissions (e.g. Stream posts) — please sign in again.")
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.refresh_token:
        try:
            creds.refresh(Request())
            secure_write(TOKEN_FILE, creds.to_json())
            return creds
        except RefreshError:
            info("Your login expired — let's sign in again.")
    return browser_login()


def signed_in_user(drive) -> str:
    try:
        u = drive.about().get(fields="user(displayName,emailAddress)").execute()["user"]
        return f"{u.get('displayName', '')} <{u.get('emailAddress', '')}>".strip()
    except Exception:
        return "your Google account"


# ───────────────────────────── classroom ─────────────────────────────

def paginate(method: Callable, key: str, **kwargs) -> Iterable[Dict]:
    token = None
    while True:
        resp = method(pageToken=token, **kwargs).execute()
        yield from resp.get(key, [])
        token = resp.get("nextPageToken")
        if not token:
            break


def list_courses(classroom) -> List[Dict]:
    courses = list(paginate(classroom.courses().list, "courses",
                            courseStates=["ACTIVE", "ARCHIVED"], pageSize=100))
    courses.sort(key=lambda c: (c.get("courseState") != "ACTIVE", c.get("name", "").lower()))
    return courses


@dataclass
class Attachment:
    file_id: str
    title: str
    folder: Tuple[str, ...]  # relative to the course folder
    source: str


@dataclass
class LinkRef:
    folder: Tuple[str, ...]
    post: str
    title: str
    url: str


@dataclass
class CourseScan:
    course: Dict
    attachments: List[Attachment] = field(default_factory=list)
    links: List[LinkRef] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _post_label(post: Dict) -> str:
    if post.get("title"):
        return post["title"]
    text = (post.get("text") or "").strip().splitlines()
    snippet = text[0][:50] if text else "Post"
    date = (post.get("creationTime") or "")[:10]
    return f"{date} {snippet}".strip()


def scan_course(classroom, course: Dict, sources: List[str], layout: str) -> CourseScan:
    cid = course["id"]
    scan = CourseScan(course)

    topics: Dict[str, str] = {}
    if layout != "flat":
        try:
            topics = {t["topicId"]: t.get("name", "Topic")
                      for t in paginate(classroom.courses().topics().list, "topic", courseId=cid)}
        except HttpError as e:
            scan.warnings.append(f"Couldn't read topics: {http_reason(e)}")

    def folder_for(post: Dict, section: Optional[str] = None) -> Tuple[str, ...]:
        if layout == "flat":
            return ()
        top = section or topics.get(post.get("topicId", ""), "No topic")
        parts = [safe_name(top)]
        if layout == "post":
            parts.append(safe_name(_post_label(post), 80))
        return tuple(parts)

    def add_materials(post: Dict, materials: List[Dict], source: str, folder: Tuple[str, ...]):
        label = _post_label(post)
        for m in materials or []:
            if "driveFile" in m:
                df = m["driveFile"].get("driveFile", {})
                if df.get("id"):
                    scan.attachments.append(Attachment(df["id"], df.get("title") or label, folder, source))
            elif "youtubeVideo" in m:
                v = m["youtubeVideo"]
                scan.links.append(LinkRef(folder, label, v.get("title", "YouTube video"), v.get("alternateLink", "")))
            elif "link" in m:
                l = m["link"]
                scan.links.append(LinkRef(folder, label, l.get("title") or l.get("url", ""), l.get("url", "")))
            elif "form" in m:
                f = m["form"]
                scan.links.append(LinkRef(folder, label, f.get("title", "Google Form"), f.get("formUrl", "")))

    def safely(what: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except HttpError as e:
            scan.warnings.append(f"Couldn't read {what}: {http_reason(e)}")

    coursework: List[Dict] = []

    def load_coursework():
        coursework.extend(paginate(classroom.courses().courseWork().list, "courseWork", courseId=cid))

    if "materials" in sources:
        def _materials():
            for m in paginate(classroom.courses().courseWorkMaterials().list, "courseWorkMaterial", courseId=cid):
                add_materials(m, m.get("materials"), "materials", folder_for(m))
        safely("classwork materials", _materials)

    if "assignments" in sources or "submissions" in sources:
        safely("assignments", load_coursework)

    if "assignments" in sources:
        for cw in coursework:
            add_materials(cw, cw.get("materials"), "assignments", folder_for(cw))

    if "stream" in sources:
        def _stream():
            for a in paginate(classroom.courses().announcements().list, "announcements", courseId=cid):
                add_materials(a, a.get("materials"), "stream", folder_for(a, "Stream"))
        safely("stream posts", _stream)

    if "submissions" in sources:
        titles = {cw["id"]: cw.get("title", "Assignment") for cw in coursework}

        def _subs():
            for s in paginate(classroom.courses().courseWork().studentSubmissions().list,
                              "studentSubmissions", courseId=cid, courseWorkId="-", userId="me"):
                atts = (s.get("assignmentSubmission") or {}).get("attachments", [])
                if not atts:
                    continue
                folder = ("My submissions", safe_name(titles.get(s.get("courseWorkId"), "Assignment"), 80))
                add_materials({"title": titles.get(s.get("courseWorkId"))}, atts, "submissions", folder)
        safely("your submissions", _subs)

    return scan


# ───────────────────────────── drive / planning ─────────────────────────────

@dataclass
class Task:
    file_id: str
    name: str
    mime: str
    size: Optional[int]
    modified: str
    rel_path: Path  # relative to base dir
    status: str = "new"  # new | updated | moved


def fetch_metadata(drive, ids: List[str]) -> Dict[str, Dict]:
    """Batch-fetch Drive metadata (100 per HTTP request). Value is a dict or an Exception."""
    out: Dict[str, Dict] = {}
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]

        def cb(request_id, response, exception):
            out[request_id] = exception if exception else response

        batch = drive.new_batch_http_request(callback=cb)
        for fid in chunk:
            batch.add(drive.files().get(fileId=fid, fields=DRIVE_FIELDS, supportsAllDrives=True),
                      request_id=fid)
        batch.execute()
    # retry rate-limited ones individually
    for fid, val in list(out.items()):
        if isinstance(val, HttpError) and http_status(val) in (403, 429, 500, 503) \
                and "rate" in http_reason(val).lower():
            time.sleep(1)
            try:
                out[fid] = drive.files().get(fileId=fid, fields=DRIVE_FIELDS, supportsAllDrives=True).execute()
            except HttpError as e:
                out[fid] = e
    return out


def list_folder(drive, folder_id: str) -> List[Dict]:
    return list(paginate(drive.files().list, "files",
                         q=f"'{folder_id}' in parents and trashed = false",
                         fields=f"nextPageToken, files({DRIVE_FIELDS})",
                         supportsAllDrives=True, includeItemsFromAllDrives=True, pageSize=1000))


class Planner:
    """Turns scanned attachments into concrete download tasks with unique paths."""

    def __init__(self, drive, base_dir: Path, index: Dict[str, Dict]):
        self.drive = drive
        self.base_dir = base_dir
        self.index = index
        self.seen: set = set()
        self.claimed: Dict[str, str] = {
            str(Path(v["path"])).lower(): fid for fid, v in index.items() if v.get("path")
        }

    def _unique(self, rel: Path, file_id: str) -> Path:
        candidate, n = rel, 2
        while True:
            owner = self.claimed.get(str(candidate).lower())
            if owner in (None, file_id):
                self.claimed[str(candidate).lower()] = file_id
                return candidate
            candidate = rel.with_name(f"{rel.stem} ({n}){rel.suffix}")
            n += 1

    def plan_course(self, scan: CourseScan) -> Tuple[List[Task], int, List[str], List[LinkRef]]:
        course_dir = Path(safe_name(scan.course.get("name") or f"course_{scan.course['id']}"))
        tasks: List[Task] = []
        skipped = 0
        problems: List[str] = []
        extra_links: List[LinkRef] = []

        todo = []
        for att in scan.attachments:
            if att.file_id in self.seen:
                continue
            self.seen.add(att.file_id)
            todo.append(att)
        meta = fetch_metadata(self.drive, [a.file_id for a in todo]) if todo else {}

        def handle(fid: str, m: Dict, folder: Tuple[str, ...], hint: str, depth: int = 0):
            nonlocal skipped
            mime = m.get("mimeType", "")
            if mime == SHORTCUT_MIME:
                target = (m.get("shortcutDetails") or {}).get("targetId")
                if target and target not in self.seen:
                    self.seen.add(target)
                    try:
                        tm = self.drive.files().get(fileId=target, fields=DRIVE_FIELDS,
                                                    supportsAllDrives=True).execute()
                        handle(target, tm, folder, m.get("name", hint), depth)
                    except HttpError as e:
                        problems.append(f"{hint}: {http_reason(e)}")
                return
            if mime == FOLDER_MIME:
                if depth > 8:
                    return
                sub = folder + (safe_name(m.get("name") or hint),)
                try:
                    children = list_folder(self.drive, fid)
                except HttpError as e:
                    problems.append(f"Folder '{m.get('name')}': {http_reason(e)}")
                    return
                for child in children:
                    if child["id"] in self.seen:
                        continue
                    self.seen.add(child["id"])
                    handle(child["id"], child, sub, child.get("name", ""), depth + 1)
                return
            if mime.startswith("application/vnd.google-apps.") and mime not in GOOGLE_EXPORTS:
                # Forms, Sites, Jamboards… can't be downloaded — keep the link instead
                extra_links.append(LinkRef(folder, "Google files that can't be downloaded",
                                           m.get("name", hint), m.get("webViewLink", "")))
                return

            name = safe_name(m.get("name") or hint or fid)
            if mime in GOOGLE_EXPORTS and not name.lower().endswith(GOOGLE_EXPORTS[mime][1]):
                name += GOOGLE_EXPORTS[mime][1]
            modified = m.get("modifiedTime", "")
            size = int(m["size"]) if m.get("size") else None

            prev = self.index.get(fid)
            if prev and (self.base_dir / prev["path"]).exists():
                if prev.get("modified") == modified or not modified:
                    skipped += 1
                    return
                tasks.append(Task(fid, name, mime, size, modified, Path(prev["path"]), "updated"))
                return

            rel = self._unique(course_dir.joinpath(*folder, name), fid)
            dest = self.base_dir / rel
            if dest.exists() and dest.stat().st_size > 0:
                # Already on disk (e.g. from v1 without an index) — adopt it
                self.index[fid] = {"path": str(rel), "modified": modified}
                skipped += 1
                return
            legacy = self.base_dir / course_dir / name  # v1 flat layout
            if folder and legacy.is_file() and legacy.stat().st_size > 0 \
                    and str(legacy.relative_to(self.base_dir)).lower() not in self.claimed:
                self.claimed[str(legacy.relative_to(self.base_dir)).lower()] = fid
                tasks.append(Task(fid, name, mime, size, modified, rel, "moved"))
                return
            tasks.append(Task(fid, name, mime, size, modified, rel))

        for att in todo:
            m = meta.get(att.file_id)
            if isinstance(m, Exception) or m is None:
                reason = http_reason(m) if m is not None else "unknown error"
                if http_status(m) == 404:
                    reason = "file was deleted or you don't have access"
                problems.append(f"{att.title}: {reason}")
                continue
            handle(att.file_id, m, att.folder, att.title)

        return tasks, skipped, problems, extra_links


# ───────────────────────────── downloading ─────────────────────────────

def download_task(drive, session: AuthorizedSession, base_dir: Path, task: Task,
                  on_progress: Callable[[int], None]) -> None:
    dest = base_dir / task.rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    if task.status == "moved":
        legacy = base_dir / task.rel_path.parts[0] / task.name
        shutil.move(str(legacy), str(dest))
        return

    part = dest.with_name(dest.name + ".part")
    try:
        if task.mime in GOOGLE_EXPORTS:
            export_mime = GOOGLE_EXPORTS[task.mime][0]
            try:
                request = drive.files().export_media(fileId=task.file_id, mimeType=export_mime)
                _stream_request(request, part, on_progress)
            except HttpError as e:
                if "exportSizeLimitExceeded" not in str(e.content) and "too large" not in http_reason(e).lower():
                    raise
                # Big docs: export links don't have the 10 MB limit
                links = drive.files().get(fileId=task.file_id, fields="exportLinks",
                                          supportsAllDrives=True).execute().get("exportLinks", {})
                url = links.get(export_mime)
                if not url:
                    raise
                with session.get(url, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            on_progress(len(chunk))
        else:
            request = drive.files().get_media(fileId=task.file_id, supportsAllDrives=True)
            _stream_request(request, part, on_progress)
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()


def _stream_request(request, part: Path, on_progress: Callable[[int], None]) -> None:
    with io.FileIO(part, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=4 * 1024 * 1024)
        done, last = False, 0
        while not done:
            status, done = downloader.next_chunk(num_retries=3)
            now = fh.tell()
            on_progress(now - last)
            last = now


def write_links_file(course_dir: Path, course_name: str, links: List[LinkRef]) -> Optional[Path]:
    links = [l for l in links if l.url]
    if not links:
        return None
    course_dir.mkdir(parents=True, exist_ok=True)
    path = course_dir / "Links.md"
    groups: Dict[str, List[LinkRef]] = {}
    for l in links:
        groups.setdefault(" / ".join(l.folder) or "General", []).append(l)
    lines = [f"# Links — {course_name}", "",
             "_Videos, websites and forms shared in this class (generated by Classroom Downloader)._", ""]
    for g, items in groups.items():
        lines += [f"## {g}", ""]
        seen = set()
        for l in items:
            if l.url in seen:
                continue
            seen.add(l.url)
            lines.append(f"- [{l.title.replace(']', ')')}]({l.url})  — _{l.post}_")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@dataclass
class RunOptions:
    course_ids: List[str]
    sources: List[str]
    layout: str
    base_dir: Path
    dry_run: bool = False
    save_links: bool = True


def run_download(classroom, drive, creds, courses: List[Dict], opts: RunOptions) -> Path:
    base_dir = opts.base_dir.expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    index_path = base_dir / INDEX_NAME
    index: Dict[str, Dict] = load_json(index_path, {}).get("files", {})
    planner = Planner(drive, base_dir, index)
    chosen = [c for c in courses if c["id"] in opts.course_ids]

    # ---- 1. scan ----
    plans = []
    with console.status("[accent]Looking through your classes…[/]", spinner="dots") as status:
        for c in chosen:
            status.update(f"[accent]Scanning[/] {escape(c.get('name', ''))}…")
            scan = scan_course(classroom, c, opts.sources, opts.layout)
            tasks, skipped, problems, extra_links = planner.plan_course(scan)
            plans.append((c, scan, tasks, skipped, problems, scan.links + extra_links))

    # ---- 2. summary ----
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", pad_edge=False)
    table.add_column("Course", style="bold", max_width=40, overflow="fold")
    table.add_column("New", justify="right", style="ok")
    table.add_column("Updated", justify="right", style="accent2")
    table.add_column("Already have", justify="right", style="muted")
    table.add_column("Links", justify="right")
    table.add_column("Size", justify="right", style="muted")
    total_tasks = 0
    total_bytes = 0
    for c, scan, tasks, skipped, problems, links in plans:
        new = sum(t.status in ("new", "moved") for t in tasks)
        upd = sum(t.status == "updated" for t in tasks)
        size = sum(t.size or 0 for t in tasks)
        total_tasks += len(tasks)
        total_bytes += size
        table.add_row(escape(c.get("name", "")), str(new or "·"), str(upd or "·"),
                      str(skipped or "·"), str(len({l.url for l in links}) or "·"), human_size(size))
    console.print()
    console.print(table)

    if opts.dry_run:
        for c, scan, tasks, *_ in plans:
            for t in tasks:
                console.print(f"  [muted]{t.status:>7}[/]  {escape(str(t.rel_path))}")
        console.print("\n[muted]Dry run — nothing was downloaded.[/]")
        return base_dir

    # ---- 3. download ----
    failures: List[Tuple[str, str]] = []
    done = 0
    if total_tasks:
        session = AuthorizedSession(creds)
        progress = Progress(
            SpinnerColumn(style="#34a853"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None, complete_style="#34a853", finished_style="#34a853"),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
        file_progress = Progress(
            TextColumn("  [muted]{task.description}[/]"),
            BarColumn(bar_width=24, complete_style="#fbbc04"),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=console,
        )
        from rich.live import Live
        with Live(Group(progress, file_progress), console=console, refresh_per_second=10):
            overall = progress.add_task("Downloading", total=total_tasks)
            for c, scan, tasks, *_ in plans:
                for t in tasks:
                    label = t.name if len(t.name) <= 50 else t.name[:47] + "…"
                    ft = file_progress.add_task(escape(label), total=t.size)
                    try:
                        download_task(drive, session, base_dir, t,
                                      lambda n, ft=ft: file_progress.advance(ft, n))
                        index[t.file_id] = {"path": str(t.rel_path), "modified": t.modified}
                        done += 1
                    except Exception as e:  # keep going — report at the end
                        failures.append((str(t.rel_path), http_reason(e)))
                    finally:
                        file_progress.remove_task(ft)
                        progress.advance(overall)
                    if done % 20 == 0:
                        save_json(index_path, {"version": 2, "files": index})

    if opts.save_links:
        for c, scan, tasks, skipped, problems, links in plans:
            write_links_file(base_dir / safe_name(c.get("name") or c["id"]), c.get("name", ""), links)

    save_json(index_path, {"version": 2, "files": index})

    # ---- 4. report ----
    problems_all = [(c.get("name", ""), p) for c, scan, _t, _s, problems, _l in plans
                    for p in problems + scan.warnings]
    console.print()
    if total_tasks == 0:
        console.print(Panel("[ok]You're all up to date — nothing new to download.[/]",
                            border_style="#34a853", box=box.ROUNDED, expand=False))
    else:
        console.print(Panel(
            f"[ok]✓ {done} file(s) saved[/]" + (f"   [err]✗ {len(failures)} failed[/]" if failures else "")
            + f"\n[muted]{escape(str(base_dir))}[/]",
            title="[accent]Done[/]", border_style="#34a853", box=box.ROUNDED, expand=False))
    if failures or problems_all:
        t = Table(box=box.MINIMAL, show_header=False, pad_edge=False)
        t.add_column(style="muted", max_width=45, overflow="fold")
        t.add_column(overflow="fold")
        for path, reason in failures:
            t.add_row(escape(path), f"[err]{escape(reason)}[/]")
        for course, p in problems_all:
            t.add_row(escape(course), f"[warn]{escape(p)}[/]")
        console.print(Panel(t, title="[warn]Couldn't get everything[/]", border_style="yellow",
                            box=box.ROUNDED, title_align="left"))
    return base_dir


# ───────────────────────────── interactive flow ─────────────────────────────

def choose_courses(courses: List[Dict], prefs: Dict) -> List[str]:
    last = set(prefs.get("course_ids", []))
    choices: List = []
    archived_header = False
    for c in courses:
        archived = c.get("courseState") != "ACTIVE"
        if archived and not archived_header:
            choices.append(Separator("── archived ──"))
            archived_header = True
        title = [("", c.get("name", "Untitled"))]
        if c.get("section"):
            title.append(("class:muted", f"  · {c['section']}"))
        choices.append(Choice(title=title, value=c["id"], checked=c["id"] in last))
    return ask(questionary.checkbox(
        "Which classes?",
        choices=choices,
        style=Q_STYLE,
        instruction="(↑↓ move · space select · a all · i invert · enter done)",
        validate=lambda x: True if x else "Pick at least one class (space to select)",
    ))


def interactive_options(courses: List[Dict], prefs: Dict) -> RunOptions:
    course_ids = choose_courses(courses, prefs)

    preset = select("What should I download?", [
        Choice("Everything (recommended)", "all"),
        Choice("Let me choose…", "custom"),
    ])
    if preset == "all":
        sources = ["materials", "assignments", "stream"]
    else:
        prev = prefs.get("sources", ["materials", "assignments", "stream"])
        sources = ask(questionary.checkbox(
            "Include:", style=Q_STYLE,
            choices=[Choice(label, key, checked=key in prev) for key, label in SOURCES.items()],
            validate=lambda x: True if x else "Pick at least one"))

    layout = select("How should files be organised?",
                    [Choice(label, key) for key, label in LAYOUTS.items()],
                    default=prefs.get("layout", "topic"))

    default_dir = prefs.get("base_dir") or str(Path.home() / "Classroom Downloads")
    where = ask(questionary.path("Save to folder:", default=default_dir, only_directories=True,
                                 style=Q_STYLE))
    base_dir = Path(where.strip().strip("'\"")).expanduser()

    prefs.update({"course_ids": course_ids, "sources": sources, "layout": layout,
                  "base_dir": str(base_dir)})
    save_json(PREFS_FILE, prefs)
    return RunOptions(course_ids, sources, layout, base_dir)


def interactive(args) -> None:
    banner()
    creds = get_credentials(force_login=args.switch_account)
    while True:
        classroom = build("classroom", "v1", credentials=creds, cache_discovery=False)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        with console.status("[accent]Connecting to Google Classroom…[/]", spinner="dots"):
            who = signed_in_user(drive)
            try:
                courses = list_courses(classroom)
            except HttpError as e:
                error(f"Couldn't load your classes: {http_reason(e)}")
                if "has not been used" in http_reason(e) or "disabled" in http_reason(e):
                    console.print("[muted]Turn on the Classroom and Drive APIs (setup steps 2 & 3).[/]")
                sys.exit(1)
        console.print(f"\n[ok]●[/] Signed in as [b]{escape(who)}[/]  "
                      f"[muted]· {len(courses)} classes[/]\n")

        action = select("What would you like to do?", [
            Choice("⬇   Download files from my classes", "download"),
            Choice("👤  Switch Google account", "switch"),
            Choice("🛠   Redo setup (new login key)", "setup"),
            Choice("👋  Quit", "quit"),
        ])
        if action == "quit":
            return
        if action == "switch":
            creds = get_credentials(force_login=True)
            continue
        if action == "setup":
            run_setup_wizard()
            creds = get_credentials(force_login=True)
            continue
        if not courses:
            warn("No classes found on this account. Try 'Switch Google account'.")
            continue

        prefs = load_json(PREFS_FILE, {})
        opts = interactive_options(courses, prefs)
        opts.dry_run = args.dry_run
        base_dir = run_download(classroom, drive, creds, courses, opts)

        nxt = select("Next:", [
            Choice("📂  Open the download folder", "open"),
            Choice("↩   Back to the menu", "menu"),
            Choice("👋  Quit", "quit"),
        ])
        if nxt == "open":
            open_path(base_dir)
            return
        if nxt == "quit":
            return


def headless(args) -> None:
    creds = get_credentials(force_login=args.switch_account)
    classroom = build("classroom", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    courses = list_courses(classroom)
    if args.all and not args.course and not args.include_archived:
        courses = [c for c in courses if c.get("courseState") == "ACTIVE"]
    if args.course:
        needles = [n.lower() for n in args.course]
        courses = [c for c in courses if any(n in c.get("name", "").lower() or n == c["id"] for n in needles)]
    if not courses:
        error("No classes matched.")
        sys.exit(1)
    info(f"{len(courses)} class(es): " + ", ".join(escape(c.get("name", "")) for c in courses))
    sources = [s for s in ("materials", "assignments", "stream", "submissions")
               if not getattr(args, f"no_{s}", False) and (s != "submissions" or args.submissions)]
    opts = RunOptions([c["id"] for c in courses], sources, args.layout,
                      Path(args.base_dir or Path.home() / "Classroom Downloads"),
                      dry_run=args.dry_run, save_links=not args.no_links)
    run_download(classroom, drive, creds, courses, opts)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="classroom_downloader",
        description="Download files from Google Classroom. Run with no arguments for the interactive menu.",
    )
    p.add_argument("--course", "--course-name-contains", action="append", metavar="NAME",
                   help="Only classes whose name contains NAME (repeatable). Runs without menus.")
    p.add_argument("--all", action="store_true", help="Download from all active classes without menus.")
    p.add_argument("--include-archived", action="store_true", help="With --all, include archived classes.")
    p.add_argument("--base-dir", help="Where to save files (default: ~/Classroom Downloads).")
    p.add_argument("--layout", choices=list(LAYOUTS), default="topic", help="Folder layout (default: topic).")
    p.add_argument("--no-stream", action="store_true", help="Skip files posted on the Stream.")
    p.add_argument("--no-materials", action="store_true", help="Skip classwork materials.")
    p.add_argument("--no-assignments", action="store_true", help="Skip assignment attachments.")
    p.add_argument("--submissions", action="store_true", help="Also download your own submitted work.")
    p.add_argument("--no-links", action="store_true", help="Don't write Links.md files.")
    p.add_argument("--dry-run", action="store_true", help="Show what would be downloaded, download nothing.")
    p.add_argument("--switch-account", action="store_true", help="Sign in with a different Google account.")
    p.add_argument("--setup", action="store_true", help="Run the one-time setup wizard again.")
    p.add_argument("--logout", action="store_true", help="Forget the saved login and exit.")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.logout:
        TOKEN_FILE.unlink(missing_ok=True)
        info("Signed out.")
        return
    if args.setup:
        banner()
        run_setup_wizard()
        args.switch_account = True
    try:
        if args.course or args.all or not sys.stdin.isatty():
            headless(args)
        else:
            interactive(args)
    except KeyboardInterrupt:
        console.print("\n[muted]Stopped. Run again any time — finished files are kept.[/]")
        sys.exit(130)


if __name__ == "__main__":
    main()
