"""Command line entry points for building, serving and reproducing the prototype."""
import argparse
from contextlib import closing
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

from .pipeline import replay, run
from .sources import MSK
from .storage import connect, ingest, ingest_archive, ingest_public

ROOT = Path(__file__).resolve().parents[1]


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
        name = Path(f.name)
        f.write(text)
    try:
        name.replace(path)
    finally:
        name.unlink(missing_ok=True)


def render(out, results):
    payload = json.dumps(results, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    template = (ROOT / "hotel" / "dashboard.html").read_text(encoding="utf-8")
    embedded = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    atomic(out / "forecast.html", template.replace("__HOTEL_DATA__", embedded))
    atomic(out / "results.json", payload)


def serve(out, port):
    routes = {"/": ("forecast.html", "text/html; charset=utf-8"),
              "/api/data": ("results.json", "application/json; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path).path
            if url == "/health":
                content = b'{"status":"ok"}' if (out/"results.json").is_file() else b'{"status":"unbuilt"}'
                kind, code = "application/json", 200 if b'"ok"' in content else 503
            elif url in routes:
                name, kind = routes[url]
                try:
                    content, code = (out/name).read_bytes(), 200
                except FileNotFoundError:
                    return self.send_error(503, "Run the build command first")
            else:
                return self.send_error(404)
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"http://127.0.0.1:{httpd.server_port}", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=ROOT/"config.json")
    ap.add_argument("--db", type=Path, default=ROOT/"build"/"hotel.sqlite")
    ap.add_argument("--out", type=Path, default=ROOT/"build")
    ap.add_argument("--archive-file", type=Path, default=ROOT/"data"/"public_events.json")
    commands = ap.add_subparsers(dest="command", required=True)
    for name in ("build", "ingest", "demo"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--as-of", type=date.fromisoformat)
        mode = cmd.add_mutually_exclusive_group()
        mode.add_argument("--public", action="store_true", help="Запросить свежую афишу KudaGo")
        mode.add_argument("--archive", action="store_true", help="Показать прогноз с публичной афишей из архива")
        if name == "demo":
            cmd.add_argument("--port", type=int, default=8765)
    commands.add_parser("serve").add_argument("--port", type=int, default=8765)
    commands.add_parser("replay").add_argument("id")
    a = ap.parse_args(argv)
    if a.command == "serve":
        serve(a.out, a.port)
        return 0
    cfg = json.loads(a.config.read_text(encoding="utf-8-sig"))
    anchor = date.fromisoformat(cfg["as_of"])
    if cfg["horizon"] != 14 or not all(cfg[n] >= 28 and cfg[n] % 14 == 0 for n in ("validation_days", "test_days")):
        ap.error("Для этого проекта горизонт 14 дней, периоды валидации и теста кратны 14 и не короче 28 дней.")
    if cfg["train_stride"] < 1 or cfg["request_retries"] < 1 or len({h["id"] for h in cfg["hotels"]}) != len(cfg["hotels"]):
        ap.error("Проверьте шаг обучения, число попыток и уникальность ID отелей.")
    spec = cfg["stress"]
    if not (spec["days"] >= 28 and spec["days"] % cfg["horizon"] == 0
            and 0 <= spec["event_scale"] <= 1 and 0 <= spec["late_share"] <= 1
            and 0 <= spec["notice_days"] < cfg["horizon"]):
        ap.error("Проверьте длину и параметры стресс-периода.")
    a.db.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(a.db, cfg)) as db:
        if a.command == "replay":
            r = replay(db, a.id)
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0 if r["reproduced"] else 1
        today = datetime.now(MSK).date()
        end = a.as_of or (today if a.public else anchor)
        if a.public and end != today:
            ap.error("--public загружает текущий снимок; архивные даты недоступны.")
        if end < anchor and a.command in ("build", "demo"):
            ap.error("Дата расчёта не может предшествовать опорной дате в config.json.")
        print(json.dumps(ingest(db, cfg, end), ensure_ascii=False))
        archive_ready = ingest_archive(db, a.archive_file, end)
        if not archive_ready:
            print("Публичный архив не загружен; см. журнал.", file=sys.stderr)
            if a.archive:
                return 1
        public = a.public and ingest_public(db, cfg, today)
        if a.public and not public:
            print("KudaGo недоступен; используется автономная афиша. Подробности в журнале.", file=sys.stderr)
        if a.command != "ingest":
            result = run(db, cfg, end, public, a.archive and archive_ready)
            render(a.out, result)
            for t, row in result["targets"].items():
                print(f"{t}: {row['winner']['family']}; test MAE={row['winner']['test']['mae']:.2f}; "
                      f"baseline={row['baseline']['test']['mae']:.2f}; ID={row['winner']['id']}")
            print(a.out / "forecast.html", flush=True)
    if a.command == "demo":
        serve(a.out, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
