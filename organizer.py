import re
from pathlib import Path
from urllib.parse import unquote_plus

_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TRAILING = re.compile(r'[. ]+$')


def decode_filename(name: str) -> str:
    """Decodifica nomes vindos do Canvas com codificação de URL.

    Ex.: 'fun%C3%A7%C3%A3o+1+Afim.py' -> 'função 1 Afim.py'
         '06_09_Exercicio+Heranca.py' -> '06_09_Exercicio Heranca.py'
    """
    try:
        decoded = unquote_plus(name)
    except Exception:
        decoded = name
    # colapsa espaços resultantes
    return re.sub(r"\s+", " ", decoded).strip()


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
    return Path(output_dir) / _course_folder(course) / tipo / sanitize(decode_filename(filename))


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
