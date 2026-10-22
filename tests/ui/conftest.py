"""Starts a real server and a real browser for the UI tests.

Run with:  python -m pytest -m ui --no-cov
Needs Chromium:  python -m playwright install chromium
"""

import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.auth import hash_token
from app.directory import InMemoryDirectory
from app.main import create_app
from app.models import Role, User

PASSWORDS = {"emma": "emma-password-1", "liam": "liam-password-1", "ava": "ava-password-1"}
PEOPLE = [
    # username, full name, department, role, manager
    ("liam", "Liam Chen", "Finance", Role.MANAGER, None),
    ("emma", "Emma Ruiz", "Finance", Role.EMPLOYEE, "liam"),
    ("ava", "Ava Patel", "IT", Role.AGENT, None),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    directory = InMemoryDirectory.with_default_groups()
    app = create_app(f"sqlite:///{tmp_path}/ui.db", directory=directory, base_url=base_url)
    with app.state.session_factory() as session:
        ids = {}
        for username, name, dept, role, manager in PEOPLE:
            first, last = name.split()
            directory.create_user(username, first, last, dept)
            directory.add_to_group(username, "all-staff")
            directory.set_password(username, PASSWORDS[username])
            user = User(
                username=username,
                full_name=name,
                department=dept,
                role=role,
                manager_id=ids.get(manager),
                token_hash=hash_token(f"{username}-ui-token"),
            )
            session.add(user)
            session.flush()
            ids[username] = user.id
        session.commit()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    yield base_url, directory
    uv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser, server, request):
    context = browser.new_context(base_url=server[0], viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(5000)
    yield page
    # Keep a picture of the last screen for the CI artifacts.
    page.screenshot(path=f"test-results/{request.node.name}.png", full_page=True)
    context.close()


def sign_in(page, username: str, password: str | None = None) -> None:
    page.goto("/login")
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password or PASSWORDS[username])
    page.get_by_role("button", name="Sign in").click()


def sign_out(page) -> None:
    page.get_by_role("button", name="Sign out").click()
