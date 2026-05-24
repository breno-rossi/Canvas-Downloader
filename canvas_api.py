import logging
import re
import time

import requests

log = logging.getLogger("canvas")

_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')

# Quando remaining cair abaixo deste valor, freia proativamente.
_LOW_WATER_MARK = 50


class CanvasAPI:
    def __init__(self, base_url: str, token: str, max_retries: int = 5, backoff: int = 30,
                 request_delay: float = 0.15):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.max_retries = max_retries
        self.backoff = backoff
        # Pausa mínima entre requisições — evita esgotar o bucket do Canvas.
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"
        self._last_request_time: float = 0.0

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)

    def _get_raw(self, url: str, params: dict | None = None) -> requests.Response:
        params = params or {}
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                wait = self.backoff * (2 ** attempt)
                log.warning("Erro de rede (tentativa %d/%d): %s. Aguardando %ds...",
                            attempt + 1, self.max_retries, exc, wait)
                time.sleep(wait)
                continue
            finally:
                self._last_request_time = time.monotonic()

            remaining_str = resp.headers.get("X-Rate-Limit-Remaining", "")

            # Distingue rate limit de erro de permissão real:
            # rate limit 403 vem com X-Rate-Limit-Remaining = 0 ou corpo específico.
            is_rate_limited = resp.status_code == 429
            if resp.status_code == 403 and remaining_str:
                try:
                    is_rate_limited = float(remaining_str) <= 0
                except ValueError:
                    pass

            if is_rate_limited:
                wait = self.backoff * (2 ** attempt)
                log.warning("Rate limit atingido (tentativa %d/%d). Aguardando %ds...",
                            attempt + 1, self.max_retries, wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()

            # Throttle proativo: se o bucket estiver baixo, pausa para recarregar.
            if remaining_str:
                try:
                    remaining = float(remaining_str)
                    log.debug("Rate limit restante: %.0f", remaining)
                    if remaining < _LOW_WATER_MARK:
                        pause = max(1.0, (_LOW_WATER_MARK - remaining) * 0.5)
                        log.info("Rate limit baixo (%.0f). Pausando %.1fs...", remaining, pause)
                        time.sleep(pause)
                except ValueError:
                    pass

            return resp

        raise RuntimeError(f"Máximo de tentativas atingido para: {url}")

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return self._get_raw(self.base + path, params)

    def _paginate(self, path: str, params: dict | None = None):
        params = {**(params or {}), "per_page": 100}
        url = self.base + path
        while url:
            resp = self._get_raw(url, params)
            data = resp.json()
            if isinstance(data, list):
                yield from data
            else:
                yield data
                return
            link_header = resp.headers.get("Link", "")
            match = _NEXT_LINK_RE.search(link_header)
            url = match.group(1) if match else None
            params = {}

    def get_courses(self) -> list[dict]:
        return list(self._paginate("/courses", {
            "include[]": ["term", "concluded"],
            "state[]": ["available", "completed"],
        }))

    def _list(self, path: str, label: str, params: dict | None = None) -> list[dict]:
        try:
            return list(self._paginate(path, params))
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code in (401, 403, 404):
                log.debug("Endpoint indisponível (%d) — %s", code, label)
                return []
            raise

    def get_course_files(self, course_id: int) -> list[dict]:
        return self._list(f"/courses/{course_id}/files",
                          f"arquivos curso {course_id}")

    def get_modules(self, course_id: int) -> list[dict]:
        return self._list(f"/courses/{course_id}/modules",
                          f"módulos curso {course_id}")

    def get_module_items(self, course_id: int, module_id: int) -> list[dict]:
        return self._list(f"/courses/{course_id}/modules/{module_id}/items",
                          f"itens módulo {module_id}")

    def get_file(self, file_id: int) -> dict | None:
        try:
            return self._get(f"/files/{file_id}").json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403, 404):
                log.debug("Arquivo %d inacessível: %d", file_id, exc.response.status_code)
                return None
            raise

    def get_assignments(self, course_id: int) -> list[dict]:
        return self._list(f"/courses/{course_id}/assignments",
                          f"tarefas curso {course_id}")

    def get_pages(self, course_id: int) -> list[dict]:
        return self._list(f"/courses/{course_id}/pages",
                          f"páginas curso {course_id}")

    def get_page_body(self, course_id: int, page_url_slug: str) -> dict | None:
        try:
            return self._get(f"/courses/{course_id}/pages/{page_url_slug}").json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403, 404):
                return None
            raise
