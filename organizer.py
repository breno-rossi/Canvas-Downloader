import re
from pathlib import Path

_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TRAILING = re.compile(r'[. ]+$')


def sanitize(name: str, max_len: int = 200) -> str:
    name = _ILLEGAL_CHARS.sub("_", name)
    name = _TRAILING.sub("", name).strip()
    return name[:max_len] if name else "_"


def _course_folder(course: dict) -> str:
    name = course.get("name") or f"Curso_{course['id']}"
    year = ""
    if course.get("start_at"):
        year = course["start_at"][:4]
    elif course.get("term") and course["term"].get("name"):
        year = course["term"]["name"]
    prefix = f"{year} - " if year else ""
    return sanitize(f"{prefix}{name}")


def build_path(output_dir: str, course: dict, tipo: str, filename: str) -> Path:
    return Path(output_dir) / _course_folder(course) / tipo / sanitize(filename)


def build_page_path(output_dir: str, course: dict, title: str) -> Path:
    return Path(output_dir) / _course_folder(course) / "Páginas" / (sanitize(title) + ".html")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
