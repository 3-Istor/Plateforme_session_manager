import asyncio

from backend.app import main
from backend.app.frontend_version import frontend_version, frontend_index_response


def test_version_matches_deployed_entry_and_changes_with_build(tmp_path):
    index = tmp_path / "index.html"
    index.write_text('<script type="module" crossorigin src="/assets/index-old.js"></script>')
    first = frontend_version(tmp_path)
    assert first["entry"] == "/assets/index-old.js"
    assert first == frontend_version(tmp_path)
    index.write_text('<script src="/assets/index-new.js" type="module"></script>')
    second = frontend_version(tmp_path)
    assert second["entry"] == "/assets/index-new.js"
    assert first["version"] != second["version"]
    assert str(tmp_path) not in str(second)


def test_missing_frontend_has_no_update_identity(tmp_path):
    assert frontend_version(tmp_path) == {"version": None, "entry": None}


def test_only_built_module_entry_is_used(tmp_path):
    (tmp_path / "index.html").write_text('<script src="https://example.com/tracker.js"></script><script type="module" src="/src/main.tsx"></script>')
    assert frontend_version(tmp_path)["entry"] is None


def test_html_is_not_cached(tmp_path):
    assert frontend_index_response(tmp_path / "index.html").headers["cache-control"] == "no-store"


def test_version_endpoint_uses_running_frontend(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text('<script type="module" src="/assets/current.js"></script>')
    monkeypatch.setattr(main, "dist_path", str(tmp_path))
    assert asyncio.run(main.deployed_frontend_version())["entry"] == "/assets/current.js"
