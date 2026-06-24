#!/usr/bin/env python3
"""
Baixador de Conteudo Canvas
Baixa todos os arquivos dos seus cursos do Canvas LMS.

Uso:
    pip install -r requirements.txt
    cp .env.example .env   # preencha CANVAS_BASE_URL e CANVAS_API_TOKEN
    python canvas_downloader.py
"""

import json
import logging
import os
import re
import sys
import unicodedata
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

try:
    from tqdm import tqdm
except ImportError:
    print("Instale as dependências: pip install -r requirements.txt")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Instale as dependências: pip install -r requirements.txt")
    sys.exit(1)

from canvas_api import CanvasAPI
from downloader import Downloader
from organizer import build_path, build_page_path, decode_filename, sanitize, unique_path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(log_file: str = "canvas_downloader.log") -> logging.Logger:
    logger = logging.getLogger("canvas")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)

    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3,
                             encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


log = _setup_logging()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> tuple[str, str, dict]:
    load_dotenv()
    base_url = os.getenv("CANVAS_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("CANVAS_API_TOKEN", "").strip()

    if not base_url or not token:
        print("\nERRO: Configure o arquivo .env com CANVAS_BASE_URL e CANVAS_API_TOKEN.")
        print("Copie .env.example para .env e preencha com seus dados.")
        sys.exit(1)

    cfg: dict = {}
    cfg_path = Path("config.json")
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    return base_url, token, cfg


# ---------------------------------------------------------------------------
# HTML parsing — extrai IDs de arquivos Canvas embutidos em HTML
# ---------------------------------------------------------------------------

_FILE_ID_RE = re.compile(r'/files/(\d+)', re.IGNORECASE)


def _normalize_name(name: str) -> str:
    """Normaliza um nome para comparação: minúsculas, sem acentos, sem extensão."""
    # decodifica codificação de URL (+ -> espaço, %XX -> caractere)
    name = decode_filename(name)
    # remove extensão (.pdf, .html, .docx, etc.) — apenas sufixos curtos
    # alfanuméricos, para não quebrar nomes com pontos como "I.A."
    name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
    # remove acentos
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    # colapsa espaços e baixa caixa
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def extract_canvas_file_ids(html: str | None) -> list[int]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    ids: set[int] = set()

    for tag in soup.find_all(["a", "img", "iframe"]):
        for attr in ("href", "src", "data-api-endpoint"):
            val = tag.get(attr, "")
            for m in _FILE_ID_RE.finditer(val):
                ids.add(int(m.group(1)))

    return list(ids)


# ---------------------------------------------------------------------------
# Coleta de metadados
# ---------------------------------------------------------------------------

def collect_records(api: CanvasAPI, courses: list[dict], cfg: dict, output_dir: str) -> list[dict]:
    dt = cfg.get("download_types", {})
    skip_locked = cfg.get("skip_locked_files", True)
    ignore_names = {_normalize_name(n) for n in cfg.get("ignore_names", [])}
    records: list[dict] = []
    seen_file_ids: set[int] = set()

    def _is_ignored(name: str | None) -> bool:
        return bool(name) and _normalize_name(name) in ignore_names

    def _add_file(file_obj: dict, dest_path: Path):
        fid = file_obj.get("id")
        if fid and fid in seen_file_ids:
            return
        if fid:
            seen_file_ids.add(fid)
        if _is_ignored(file_obj.get("filename")) or _is_ignored(file_obj.get("display_name")):
            log.debug("Arquivo ignorado (ignore_names): %s", file_obj.get("filename"))
            return
        if skip_locked and file_obj.get("locked_for_user"):
            log.debug("Arquivo bloqueado, pulando: %s", file_obj.get("filename"))
            return
        url = file_obj.get("url") or file_obj.get("download_url")
        if not url:
            return
        dest = unique_path(dest_path)
        records.append({
            "url": url,
            "dest": dest,
            "size": file_obj.get("size"),
            "name": file_obj.get("filename", dest.name),
        })

    def _resolve_and_add(file_id: int, dest_path: Path):
        if file_id in seen_file_ids:
            return
        file_obj = api.get_file(file_id)
        if file_obj:
            _add_file(file_obj, dest_path)

    for course in tqdm(courses, desc="Escaneando cursos", unit="curso"):
        cid = course["id"]
        cname = course.get("name", f"Curso_{cid}")
        log.info("Escaneando: %s", cname)

        # --- Arquivos do curso ---
        if dt.get("course_files", True):
            for f in api.get_course_files(cid):
                dest = build_path(output_dir, course, "Arquivos", f.get("filename", str(f["id"])))
                _add_file(f, dest)

        # --- Módulos ---
        if dt.get("module_files", True):
            for module in api.get_modules(cid):
                mod_name = sanitize(module.get("name", f"Modulo_{module['id']}"))
                for item in api.get_module_items(cid, module["id"]):
                    itype = item.get("type")
                    if itype == "File":
                        fid = item.get("content_id")
                        if fid:
                            file_obj = api.get_file(fid)
                            if file_obj:
                                dest = build_path(output_dir, course,
                                                  f"Módulos/{mod_name}",
                                                  file_obj.get("filename", str(fid)))
                                _add_file(file_obj, dest)

        # --- Tarefas ---
        if dt.get("assignment_attachments", True):
            for assignment in api.get_assignments(cid):
                a_name = sanitize(assignment.get("name", f"Tarefa_{assignment['id']}"))
                description = assignment.get("description") or ""
                for fid in extract_canvas_file_ids(description):
                    file_obj = api.get_file(fid)
                    if file_obj:
                        dest = build_path(output_dir, course,
                                          f"Tarefas/{a_name}",
                                          file_obj.get("filename", str(fid)))
                        _add_file(file_obj, dest)

        # --- Páginas Wiki ---
        if dt.get("wiki_pages", True):
            for page in api.get_pages(cid):
                page_data = api.get_page_body(cid, page["url"])
                if not page_data:
                    continue
                title = page_data.get("title") or page.get("title") or f"pagina_{page['url']}"
                body = page_data.get("body") or ""

                if _is_ignored(title):
                    log.debug("Página ignorada (ignore_names): %s", title)
                    continue

                # Salva a página como HTML
                html_dest = build_page_path(output_dir, course, title)
                html_dest = unique_path(html_dest)
                records.append({"html": body, "dest": html_dest, "name": title + ".html"})

                # Extrai arquivos embutidos na página
                for fid in extract_canvas_file_ids(body):
                    file_obj = api.get_file(fid)
                    if file_obj:
                        dest = build_path(output_dir, course,
                                          "Páginas/Arquivos",
                                          file_obj.get("filename", str(fid)))
                        _add_file(file_obj, dest)

    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    base_url, token, cfg = load_config()
    output_dir = cfg.get("output_dir", "Downloads")

    print(f"\nCanvas Downloader")
    print(f"Servidor: {base_url}")
    print(f"Destino:  {Path(output_dir).resolve()}\n")

    api = CanvasAPI(
        base_url=base_url,
        token=token,
        max_retries=cfg.get("max_retries", 5),
        backoff=cfg.get("rate_limit_backoff_seconds", 30),
        request_delay=cfg.get("request_delay_seconds", 0.15),
    )
    dl = Downloader(chunk_size=cfg.get("chunk_size_bytes", 524288))

    print("Conectando ao Canvas e listando cursos...")
    try:
        all_courses = api.get_courses()
    except Exception as exc:
        print(f"\nERRO ao conectar: {exc}")
        print("Verifique se CANVAS_BASE_URL e CANVAS_API_TOKEN estão corretos no .env")
        sys.exit(1)

    filter_ids = [int(i) for i in cfg.get("course_ids", [])]
    if filter_ids:
        courses = [c for c in all_courses if c["id"] in filter_ids]
    else:
        courses = all_courses

    if not courses:
        print("Nenhum curso encontrado. Verifique se o token está correto e tem cursos ativos.")
        sys.exit(0)

    print(f"Cursos encontrados: {len(courses)}")
    for c in courses:
        print(f"  • {c.get('name', c['id'])}")

    print("\nColetando lista de arquivos (pode demorar alguns minutos)...")
    records = collect_records(api, courses, cfg, output_dir)

    if not records:
        print("Nenhum arquivo encontrado.")
        sys.exit(0)

    file_records = [r for r in records if "url" in r]
    html_records = [r for r in records if "html" in r]
    print(f"\nTotal: {len(file_records)} arquivo(s) + {len(html_records)} página(s) wiki\n")

    stats = {"downloaded": 0, "skipped": 0, "errors": 0}

    with tqdm(total=len(records), desc="Baixando", unit="item") as pbar:
        for record in records:
            try:
                if "html" in record:
                    result = dl.save_html(record["html"], record["dest"])
                else:
                    result = dl.download_file(record["url"], record["dest"], record.get("size"))
                stats[result] = stats.get(result, 0) + 1
            except Exception as exc:
                name = record.get("name", str(record.get("dest", "?")))
                log.error("Erro ao baixar '%s': %s", name, exc)
                stats["errors"] += 1
            finally:
                pbar.update(1)

    print(f"\nConcluído!")
    print(f"  Baixados : {stats.get('downloaded', 0)}")
    print(f"  Pulados  : {stats.get('skipped', 0)}  (já existiam)")
    print(f"  Erros    : {stats.get('errors', 0)}")
    if stats.get("errors", 0) > 0:
        print("  -> Verifique canvas_downloader.log para detalhes dos erros.")

    print(f"\nArquivos salvos em: {Path(output_dir).resolve()}")


if __name__ == "__main__":
    main()
