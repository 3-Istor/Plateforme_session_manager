"""Public frontend identity, derived from the files in the running image."""
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from starlette.responses import FileResponse


def frontend_index_response(path):
    return FileResponse(path, headers={"Cache-Control": "no-store"})


class EntryParser(HTMLParser):
    entry: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        src = attrs.get("src", "")
        if tag == "script" and attrs.get("type") == "module" and src.startswith("/assets/") and src.endswith(".js"):
            self.entry = src


def frontend_version(dist_path):
    try:
        contents = (Path(dist_path) / "index.html").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {"version": None, "entry": None}
    parser = EntryParser()
    parser.feed(contents)
    return {"version": hashlib.sha256(contents.encode()).hexdigest()[:16], "entry": parser.entry}
