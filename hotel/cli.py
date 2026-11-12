"""Command line entry points for building, serving and reproducing the prototype."""

import argparse
import json
import sys
import tempfile
import webbrowser
from contextlib import closing
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .live import SOURCE_NAMES, load_live_schedule
from .pipeline import replay, run
from .rates import collect_market_rates
from .sources import MSK
from .storage import connect, ingest, ingest_archive

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_SOURCE = ROOT / "hotel"
DEFAULT_PORT = 8765
RATE_ARCHIVE = ROOT / "data" / "public_rates.json"
REAL_ADR_VALIDATION = ROOT / "data" / "real_adr_validation.json"


def write_atomic(path, text):
    """Publish a complete file so a reader never sees a partial response."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as f:
        name = Path(f.name)
        f.write(text)
    try:
        name.replace(path)
    finally:
        name.unlink(missing_ok=True)


def render(out, results):
    payload = json.dumps(results, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    template = (DASHBOARD_SOURCE / "dashboard.html").read_text(encoding="utf-8")
    styles = (DASHBOARD_SOURCE / "dashboard.css").read_text(encoding="utf-8").strip()
    script = (DASHBOARD_SOURCE / "dashboard.js").read_text(encoding="utf-8").strip()
    # JSON lives inside a script element. Escape characters that could end it.
    embedded = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = (
        template.replace("__HOTEL_STYLES__", styles, 1)
        .replace("__HOTEL_SCRIPT__", script, 1)
        .replace("__HOTEL_DATA__", embedded, 1)
    )
    write_atomic(out / "forecast.html", html)
    write_atomic(out / "results.json", payload)


def serve(out, port, *, open_browser=False):
    routes = {
        "/": ("forecast.html", "text/html; charset=utf-8"),
        "/api/data": ("results.json", "application/json; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/health":
                ready = (out / "results.json").is_file()
                content = b'{"status":"ok"}' if ready else b'{"status":"unbuilt"}'
                kind, code = "application/json", 200 if ready else 503
            elif route in routes:
                name, kind = routes[route]
                try:
                    content, code = (out / name).read_bytes(), 200
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
        url = f"http://127.0.0.1:{httpd.server_port}"
        print(url, flush=True)
        if open_browser:
            try:
                opened = webbrowser.open(url)
            except webbrowser.Error:
                opened = False
            if not opened:
                print("Откройте указанный адрес в браузере.", file=sys.stderr)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--db", type=Path, default=ROOT / "build" / "hotel.sqlite")
    parser.add_argument("--out", type=Path, default=ROOT / "build")
    parser.add_argument("--archive-file", type=Path, default=ROOT / "data" / "public_events.json")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "ingest", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--as-of", type=date.fromisoformat)
        mode = command.add_mutually_exclusive_group()
        mode.add_argument(
            "--public", action="store_true", help="Загрузить афишу и публичные тарифы"
        )
        mode.add_argument("--offline", action="store_true", help="Рассчитать без сети")
        mode.add_argument(
            "--archive", action="store_true", help="Показать прогноз с публичной афишей из архива"
        )
        if name == "demo":
            add_server_options(command)
    add_server_options(commands.add_parser("serve"))
    commands.add_parser("replay").add_argument("id")
    return parser


def add_server_options(command):
    command.add_argument("--port", type=int, default=DEFAULT_PORT)
    command.add_argument("--open-browser", action="store_true", help="Открыть дашборд в браузере")


def read_config(path):
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    date.fromisoformat(config["start"])
    date.fromisoformat(config["as_of"])
    horizon = config["horizon"]
    if horizon != 14:
        raise ValueError("Горизонт должен быть равен 14 дням.")
    for period in ("validation_days", "test_days", "audit_days", "confirmation_days"):
        if config[period] < 28 or config[period] % horizon:
            raise ValueError(f"{period} должен быть кратен 14 и не короче 28 дней.")
    hotel_ids = [hotel["id"] for hotel in config["hotels"]]
    if not hotel_ids or len(set(hotel_ids)) != len(hotel_ids):
        raise ValueError("Укажите гостиницы с уникальными ID.")
    if config["train_stride"] < 1 or config["request_retries"] < 1:
        raise ValueError("Шаг обучения и число попыток должны быть положительными.")
    stress = config["stress"]
    if not (
        stress["days"] >= 28
        and stress["days"] % horizon == 0
        and 0 <= stress["event_scale"] <= 1
        and 0 <= stress["late_share"] <= 1
        and 0 <= stress["notice_days"] < horizon
    ):
        raise ValueError("Проверьте длину и параметры стресс-периода.")
    return config


def build_dashboard(db, config, options, end, today):
    print(json.dumps(ingest(db, config, end), ensure_ascii=False))
    archive_ready = ingest_archive(db, options.archive_file, end)
    if not archive_ready:
        print("Публичный архив не загружен. Подробности в журнале.", file=sys.stderr)
        if options.archive:
            return 1
    live_schedule = None
    use_archive = options.archive and archive_ready
    if options.public:
        print("Загружаю актуальную афишу Москвы", flush=True)
        live_schedule = load_live_schedule(db, config, today)
        if live_schedule:
            print(f"Афиша загружена из {SOURCE_NAMES[live_schedule.source]}.", flush=True)
        else:
            use_archive = archive_ready
            fallback = "локальный архив" if use_archive else "учебная афиша"
            print(f"Онлайн-афиша недоступна. Используется {fallback}.", file=sys.stderr)
        print("Загружаю публичные тарифы гостиниц", flush=True)
    rates = collect_market_rates(db, config, RATE_ARCHIVE, today, online=options.public)
    if options.command != "ingest":
        result = run(db, config, end, archive=use_archive, live_schedule=live_schedule)
        result["market_rates"] = rates
        if REAL_ADR_VALIDATION.is_file():
            result["real_adr_validation"] = json.loads(
                REAL_ADR_VALIDATION.read_text(encoding="utf-8")
            )
        if options.public:
            result["online_schedule"] = {
                "source": result["event_source"],
                "checked_at": live_schedule.checked_at
                if live_schedule
                else datetime.now(MSK).isoformat(),
                "status": "live" if live_schedule else "fallback",
            }
        render(options.out, result)
        for target, metrics in result["targets"].items():
            winner = metrics["winner"]
            baseline = metrics["baseline"]["test"]["mae"]
            print(
                f"{target}: {winner['family']}; test MAE={winner['test']['mae']:.2f}; "
                f"baseline={baseline:.2f}; ID={winner['id']}"
            )
        print(options.out / "forecast.html", flush=True)
    return 0


def main(argv=None):
    parser = make_parser()
    options = parser.parse_args(argv)
    if hasattr(options, "port") and not 0 <= options.port <= 65535:
        parser.error("Порт должен быть целым числом от 0 до 65535.")
    if options.command == "serve":
        try:
            serve(options.out, options.port, open_browser=options.open_browser)
        except OSError as error:
            parser.exit(1, f"Не удалось запустить сервер на порту {options.port}: {error}\n")
        return 0
    try:
        config = read_config(options.config)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.error(f"Ошибка в {options.config}: {error}")

    anchor = date.fromisoformat(config["as_of"])
    if options.command != "replay":
        today = datetime.now(MSK).date()
        if (
            options.command == "demo"
            and not options.offline
            and not options.archive
            and options.as_of in (None, today)
        ):
            options.public = True
        end = options.as_of or (today if options.public else anchor)
        if options.public and end != today:
            parser.error("--public загружает снимок только на текущую дату.")
        if end < anchor and options.command in ("build", "demo"):
            parser.error("Дата расчёта не может предшествовать опорной дате в config.json.")

    options.db.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(connect(options.db, config)) as db:
            if options.command == "replay":
                result = replay(db, options.id)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["reproduced"] else 1
            status = build_dashboard(db, config, options, end, today)
            if status != 0:
                return status
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    if options.command == "demo":
        try:
            serve(options.out, options.port, open_browser=options.open_browser)
        except OSError as error:
            parser.exit(1, f"Не удалось запустить сервер на порту {options.port}: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
