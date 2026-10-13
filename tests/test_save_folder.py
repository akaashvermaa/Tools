import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aura import folder_dialog, server, yt_jobs  # noqa: E402

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture()
def client():
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_validate_creates_missing_folder_and_leaves_no_probe_file(tmp_path):
    target = tmp_path / "Music" / "My 100% playlist"
    assert folder_dialog.validate_save_dir(str(target)) == os.path.normpath(str(target))
    assert target.is_dir() and list(target.iterdir()) == []


def test_validate_accepts_quotes_pasted_from_explorer(tmp_path):
    assert folder_dialog.validate_save_dir(f'"{tmp_path}"') == os.path.normpath(str(tmp_path))


def test_validate_rejects_relative_and_unusable_paths(tmp_path):
    with pytest.raises(ValueError, match="full folder path"):
        folder_dialog.validate_save_dir("Music")
    with pytest.raises(ValueError, match="full folder path"):
        folder_dialog.validate_save_dir("")
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    with pytest.raises(ValueError, match="Can't save to that folder"):
        folder_dialog.validate_save_dir(str(a_file / "sub"))


def test_pick_folder_route(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(folder_dialog, "choose_folder", lambda start=None: seen.update(start=start) or "D:/Music")
    res = client.post("/api/pick-folder", json={"start": "D:/Old"}, headers=SAME_ORIGIN)
    assert res.get_json() == {"path": "D:/Music"} and seen["start"] == "D:/Old"

    monkeypatch.setattr(folder_dialog, "choose_folder", lambda start=None: None)  # user pressed Cancel
    assert client.post("/api/pick-folder", json={}, headers=SAME_ORIGIN).get_json() == {"path": None}

    def unavailable(start=None):
        raise folder_dialog.DialogUnavailable("No folder picker is available on this system.")

    monkeypatch.setattr(folder_dialog, "choose_folder", unavailable)
    res = client.post("/api/pick-folder", json={}, headers=SAME_ORIGIN)
    assert res.status_code == 400 and "picker" in res.get_json()["error"]


def test_pick_folder_is_blocked_cross_site(client):
    assert client.post("/api/pick-folder", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_download_rejects_a_bad_save_folder_before_starting(client, monkeypatch):
    started = []
    monkeypatch.setattr(yt_jobs, "start_download", lambda *a, **k: started.append(1))
    res = client.post("/api/yt/download", json={"url": "https://www.youtube.com/watch?v=x", "save_dir": "relative/path"},
                      headers=SAME_ORIGIN)
    assert res.status_code == 400 and "full folder path" in res.get_json()["error"] and not started


def test_download_passes_the_validated_folder_through(client, monkeypatch, tmp_path):
    got = {}

    class Fake:
        id = "abc"

    monkeypatch.setattr(yt_jobs, "start_download", lambda *a, **k: got.update(k) or Fake())
    res = client.post("/api/yt/download", json={"url": "https://www.youtube.com/watch?v=x", "save_dir": str(tmp_path / "new")},
                      headers=SAME_ORIGIN)
    assert res.status_code == 200 and got["save_dir"] == os.path.normpath(str(tmp_path / "new"))
    client.post("/api/yt/download", json={"url": "https://www.youtube.com/watch?v=x"}, headers=SAME_ORIGIN)
    assert got["save_dir"] is None                                   # empty = browser download as before


def test_page_has_a_save_to_row_on_every_downloader(client):
    html = client.get("/").get_data(as_text=True)
    for prefix in ("yt", "fb", "ig"):
        assert f'id="{prefix}-savedir"' in html and f'id="{prefix}-browse"' in html
