"""Build the click-through demo: saved screens from the real portal, as a static site.

Starts the real app (SQLite and the in-memory directory), signs in with a real
browser, and walks through a password reset, an access request with a manager's
approval, and the IT audit log. Each screen is saved as a plain HTML page with a
bar on top that explains the step. The result needs no server, so it can sit on
GitHub Pages or any static host.

    python scripts/build_demo.py --out demo
    python -m http.server --directory demo     # then open http://127.0.0.1:8000

Needs Chromium once:  python -m playwright install chromium
"""

import argparse
import html
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from playwright.sync_api import Browser, Locator, Page, sync_playwright  # noqa: E402

from app.auth import hash_token  # noqa: E402
from app.directory import InMemoryDirectory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Role, User  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "demo_assets"
REPO_URL = "https://github.com/Abhirup-code/HelpDesk_Autopilot"
# Links in the saved inbox point here, never at a real server.
PUBLIC_URL = "https://helpdesk.example"
PASSWORD = "demo-only-password-1"  # noqa: S105 (throwaway, the database is deleted after the run)
NEW_PASSWORD = "demo-only-password-2"  # noqa: S105
PEOPLE = [
    # username, full name, department, role, manager
    ("liam", "Liam Chen", "Finance", Role.MANAGER, None),
    ("emma", "Emma Ruiz", "Finance", Role.EMPLOYEE, "liam"),
    ("ava", "Ava Patel", "IT", Role.AGENT, None),
]
CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'"
)

# Runs in the page just before it is saved. Typed values only live in the browser,
# so they are copied into the HTML. Everything that could reach a server is removed.
FREEZE = """
(hot) => {
  document.querySelectorAll("script, input[type=hidden]").forEach((el) => el.remove());
  document.querySelectorAll("input, textarea").forEach((el) => {
    if (el.type === "password") el.setAttribute("value", el.value ? "x".repeat(12) : "");
    else if (el.tagName === "TEXTAREA") el.textContent = el.value;
    else el.setAttribute("value", el.value);
    el.setAttribute("readonly", "");
    el.removeAttribute("autofocus");
    el.removeAttribute("name");
  });
  document.querySelectorAll("option").forEach((el) => {
    if (el.selected) el.setAttribute("selected", ""); else el.removeAttribute("selected");
  });
  document.querySelectorAll("form").forEach((el) => {
    el.removeAttribute("action");
    el.removeAttribute("method");
  });
  document.querySelectorAll("a[href]").forEach((el) => {
    el.setAttribute("href", "#");
    el.removeAttribute("target");
  });
  document.querySelectorAll("link[rel=stylesheet]").forEach((el) => {
    el.setAttribute("href", "portal.css");
  });
  if (hot) hot.setAttribute("data-demo-hot", "");
}
"""


@dataclass
class Screen:
    chapter: str
    caption: str
    html: str
    has_hot: bool


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def running_app(workdir: Path):
    """The real app on a free local port, with the three demo people."""
    port = free_port()
    directory = InMemoryDirectory.with_default_groups()
    app = create_app(f"sqlite:///{workdir}/demo.db", directory=directory, base_url=PUBLIC_URL)
    with app.state.session_factory() as session:
        ids: dict[str, int] = {}
        for username, name, dept, role, manager in PEOPLE:
            first, last = name.split()
            directory.create_user(username, first, last, dept)
            directory.add_to_group(username, "all-staff")
            directory.set_password(username, PASSWORD)
            user = User(
                username=username,
                full_name=name,
                department=dept,
                role=role,
                manager_id=ids.get(manager),
                token_hash=hash_token(f"{username}-demo-token"),
            )
            session.add(user)
            session.flush()
            ids[username] = user.id
        session.commit()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class Recorder:
    def __init__(self, page: Page):
        self.page = page
        self.screens: list[Screen] = []
        self.chapter = ""

    def snap(self, caption: str, hot: Locator | None = None) -> None:
        """Save the screen as it is now. `hot` is the control the visitor clicks next."""
        handle = hot.element_handle() if hot is not None else None
        self.page.evaluate(FREEZE, handle)
        self.screens.append(Screen(self.chapter, caption, self.page.content(), hot is not None))
        if hot is not None:
            # The saved copy keeps the highlight; the live page carries on without it.
            hot.evaluate("el => el.removeAttribute('data-demo-hot')")

    def fill_login(self, username: str, password: str) -> Locator:
        self.page.goto("/login")
        self.page.get_by_label("Username").fill(username)
        self.page.get_by_label("Password").fill(password)
        return self.page.get_by_role("button", name="Sign in")

    def sign_in(self, username: str, password: str) -> None:
        self.fill_login(username, password).click()
        self.page.wait_for_url("**/tickets")

    def sign_out(self) -> None:
        self.page.goto("/tickets")
        self.page.get_by_role("button", name="Sign out").click()


def record(page: Page) -> list[Screen]:
    """Walk through the story. Every click here is a real request to the real app."""
    r = Recorder(page)

    r.chapter = "Password reset"
    button = r.fill_login("emma", PASSWORD)
    r.snap("Emma needs a new password. She signs in to the help desk portal.", button)
    # Each saved screen has its form fields frozen, so reload before the real click.
    r.sign_in("emma", PASSWORD)
    link = page.get_by_role("link", name="New request").first
    r.snap("Her ticket list is empty. She starts a new request.", link)

    submit_and_snap(
        r,
        "She picks Password reset and says why. That is the whole form.",
        page.get_by_role("button", name="Submit request"),
        {
            "type": "password_reset",
            "Title": "Need a new email password",
            "Description": "My password expires this week",
        },
    )
    r.snap(
        "Done automatically, in under a second. Nobody in IT touched it, "
        "and the history shows what happened.",
        page.get_by_role("link", name="Inbox"),
    )

    page.goto("/inbox")
    link = page.locator(".message a").first
    reset_path = re.sub(r"^https?://[^/]+", "", link.get_attribute("href"))
    r.snap(
        "A one-time link is in her inbox, which stands in for email here. "
        "It works once and expires in 15 minutes.",
        link,
    )

    page.goto(reset_path)
    page.get_by_label("New password").fill(NEW_PASSWORD)
    page.get_by_label("Type it again").fill(NEW_PASSWORD)
    r.snap(
        "She chooses a new password. It goes straight to the company directory, "
        "which stores only a hash. The app never keeps it.",
        page.get_by_role("button", name="Set password"),
    )
    page.goto(reset_path)
    page.get_by_label("New password").fill(NEW_PASSWORD)
    page.get_by_label("Type it again").fill(NEW_PASSWORD)
    page.get_by_role("button", name="Set password").click()
    page.wait_for_url("**/login")
    r.snap("Password set. One form, no waiting, no IT time.")

    r.chapter = "Access with approval"
    r.sign_in("emma", NEW_PASSWORD)
    submit_and_snap(
        r,
        "Next, Emma asks for access to Tableau. Access always needs a manager's yes.",
        page.get_by_role("button", name="Submit request"),
        {
            "type": "access_request",
            "Title": "Need Tableau",
            "Description": "For the month-end reports",
            "Group": "tableau-users",
        },
    )
    r.snap(
        "The ticket waits for her manager, Liam. The SLA clock is paused, "
        "because IT can't act until he decides."
    )

    r.sign_out()
    r.sign_in("liam", PASSWORD)
    page.get_by_role("link", name="Approvals").click()
    r.snap(
        "Liam signs in and finds the request waiting for him. "
        "Nobody can approve their own request.",
        page.get_by_role("button", name="Approve"),
    )
    page.goto("/approvals")
    page.get_by_role("button", name="Approve").click()
    page.wait_for_selector(".flash")
    r.snap("Approved. Emma was added to the group automatically, with no ticket for IT to work.")

    r.chapter = "The IT view"
    r.sign_out()
    r.sign_in("ava", PASSWORD)
    page.goto("/tickets?show=all")
    r.snap(
        "Ava from IT sees every ticket. Both of Emma's were closed without IT doing anything.",
        page.get_by_role("link", name="Audit log"),
    )
    page.goto("/audit")
    r.snap(
        "Every change is in the audit log: who, what, when, and for which ticket. "
        "The database itself refuses to edit or delete these records."
    )
    return r.screens


def submit_and_snap(r: Recorder, caption: str, button: Locator, fields: dict[str, str]) -> None:
    """Fill the new request form, save it, then fill it again and really submit it."""

    def fill() -> None:
        r.page.goto("/tickets/new")
        r.page.get_by_label("What do you need?").select_option(fields["type"])
        r.page.get_by_label("Title").fill(fields["Title"])
        r.page.get_by_label("Description").fill(fields["Description"])
        if "Group" in fields:
            r.page.get_by_label("Group").select_option(fields["Group"])

    fill()
    r.snap(caption, button)
    fill()
    button.click()
    r.page.wait_for_url(re.compile(r"/tickets/\d+$"))


def file_name(index: int) -> str:
    return "index.html" if index == 0 else f"{index + 1:02d}.html"


def bar(screens: list[Screen], index: int, exit_url: str) -> str:
    screen, total = screens[index], len(screens)
    last = index == total - 1
    back = f'href="{file_name(index - 1)}"' if index else 'href="#" aria-disabled="true"'
    if last:
        forward = f'<a class="demo-next" href="{html.escape(REPO_URL)}">See the code</a>'
    else:
        hot = "" if screen.has_hot else " data-demo-hot"
        forward = f'<a class="demo-next" href="{file_name(index + 1)}"{hot}>Next</a>'
    restart = '<a href="index.html">Start over</a>' if last else ""
    return f"""
  <aside class="demo-bar" aria-label="Demo guide">
    <p class="demo-chapter"><span class="demo-tag">Demo</span>{html.escape(screen.chapter)} · \
saved screens from the real app, nothing here is sent anywhere</p>
    <p class="demo-caption">{html.escape(screen.caption)}</p>
    <nav class="demo-nav" aria-label="Demo steps">
      <span class="demo-count">{index + 1} / {total}</span>
      <a class="demo-back" {back}>Back</a>
      {forward}
      {restart}
      <a href="{html.escape(exit_url)}">Exit</a>
    </nav>
    <progress class="demo-progress" value="{index + 1}" max="{total}">\
Step {index + 1} of {total}</progress>
  </aside>
  <p class="demo-hint" role="status" hidden>\
This is a demo. Click the highlighted button, or Next.</p>"""


def finish(screens: list[Screen], index: int, exit_url: str) -> str:
    """Turn one saved screen into a page of the static site."""
    page = screens[index].html
    # One-time links are random; a fixed name keeps secrets out of the saved pages.
    page = re.sub(r"/reset/[A-Za-z0-9_-]+", "/reset/one-time-link", page)
    head = (
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
        '  <meta name="robots" content="noindex">\n'
        '  <link rel="stylesheet" href="demo.css">\n'
        '  <script src="demo.js" defer></script>\n'
    )
    page = page.replace("</head>", f"  {head}</head>", 1)
    page = page.replace("<title>", "<title>Demo · ", 1)
    return re.sub(
        r"<body[^>]*>", lambda m: m.group(0) + bar(screens, index, exit_url), page, count=1
    )


def write_site(screens: list[Screen], out: Path, exit_url: str) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for index in range(len(screens)):
        (out / file_name(index)).write_text(finish(screens, index, exit_url), encoding="utf-8")
    shutil.copy(ROOT / "app" / "static" / "portal.css", out / "portal.css")
    for asset in ("demo.css", "demo.js"):
        shutil.copy(ASSETS / asset, out / asset)


def record_with(browser: Browser, base_url: str) -> list[Screen]:
    context = browser.new_context(
        base_url=base_url, viewport={"width": 1280, "height": 800}, color_scheme="light"
    )
    page = context.new_page()
    page.set_default_timeout(10_000)
    try:
        return record(page)
    finally:
        context.close()


def build(out: Path, exit_url: str = REPO_URL, browser: Browser | None = None) -> int:
    """Record the walkthrough and write it to `out`. Returns the number of screens.

    Pass a `browser` to reuse one that is already open (the tests do).
    """
    with tempfile.TemporaryDirectory() as tmp, running_app(Path(tmp)) as base_url:
        if browser is not None:
            screens = record_with(browser, base_url)
        else:
            with sync_playwright() as p:
                own = p.chromium.launch()
                screens = record_with(own, base_url)
                own.close()
    write_site(screens, out, exit_url)
    return len(screens)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the click-through demo as a static site.")
    parser.add_argument("--out", default="demo", help="Folder to write (replaced if it exists)")
    parser.add_argument("--exit-url", default=REPO_URL, help="Where the Exit link goes")
    args = parser.parse_args(argv)
    count = build(Path(args.out), args.exit_url)
    print(f"Wrote {count} screens to {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
