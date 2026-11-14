"""The click-through demo is built from the real app, so it is tested like the app."""

import re

import pytest

from scripts.build_demo import build

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def site(tmp_path_factory, browser):
    out = tmp_path_factory.mktemp("demo")
    count = build(out, exit_url="../", browser=browser)
    return out, count


def test_the_demo_tells_the_whole_story(site):
    out, count = site
    pages = sorted(p.name for p in out.glob("*.html"))
    assert count == len(pages) == 13
    assert {"portal.css", "demo.css", "demo.js"} <= {p.name for p in out.iterdir()}
    first, last = (out / "index.html").read_text(), (out / "13.html").read_text()
    assert "Sign in" in first and "Emma needs a new password" in first
    assert "Audit log" in last and "group.add" in last and "password.set" in last
    assert "Done automatically" in (out / "04.html").read_text()
    assert "Waiting for manager" in (out / "09.html").read_text()


def test_every_screen_has_one_thing_to_click_and_a_way_on(site):
    out, count = site
    for index in range(count):
        name = "index.html" if index == 0 else f"{index + 1:02d}.html"
        page = (out / name).read_text()
        assert f"{index + 1} / {count}" in page, name
        assert 'class="demo-next"' in page, name
        if index < count - 1:
            assert page.count("data-demo-hot") == 1, name
            assert f'href="{index + 2:02d}.html"' in page, name


def test_saved_screens_cannot_reach_a_server_or_leak_a_secret(site):
    out, _ = site
    for path in out.glob("*.html"):
        page = path.read_text()
        assert "csrf" not in page, path.name
        assert 'type="hidden"' not in page, path.name
        assert "127.0.0.1" not in page, path.name
        assert "demo-only-password" not in page, path.name
        assert re.findall(r"<script[^>]*>", page) == ['<script src="demo.js" defer>'], path.name
        assert not re.search(r"<form[^>]*\saction=", page), path.name
        assert "form-action 'none'" in page, path.name
        # Random one-time links are replaced with a fixed name.
        assert set(re.findall(r"/reset/[\w-]+", page)) <= {"/reset/one-time-link"}, path.name
