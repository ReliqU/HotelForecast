"""Set up a local Python environment and start the dashboard on Windows."""

import argparse
import os
import subprocess
import sys
import venv
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = PROJECT_ROOT / ".venv"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"


def project_python():
    """Create the project environment on first use and return its interpreter."""
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    interpreter = ENVIRONMENT / scripts / executable
    if not interpreter.is_file():
        print("Создаю окружение .venv", flush=True)
        venv.create(ENVIRONMENT, with_pip=True)
    return interpreter


def install_dependencies(interpreter):
    # Verify both the version and that the wheel imports successfully.
    numpy_pin = next(
        line.removeprefix("numpy==").strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.startswith("numpy==")
    )
    check = subprocess.run(
        [
            interpreter,
            "-c",
            "import numpy, sys; sys.exit(numpy.__version__ != sys.argv[1])",
            numpy_pin,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        print("Устанавливаю зависимости из requirements.txt", flush=True)
        subprocess.run(
            [interpreter, "-X", "utf8", "-m", "pip", "install", "-r", REQUIREMENTS],
            check=True,
            cwd=PROJECT_ROOT,
        )


def main():
    parser = argparse.ArgumentParser(description="Запуск дашборда «Горизонт»")
    parser.add_argument("--port", type=int, default=8765, help="Порт локального сервера")
    parser.add_argument(
        "--as-of", type=date.fromisoformat, help="Дата расчёта в формате ГГГГ-ММ-ДД"
    )
    parser.add_argument("--no-browser", action="store_true", help="Вывести адрес в консоль")
    options = parser.parse_args()
    if not (3, 11) <= sys.version_info[:2] <= (3, 14):
        parser.error("Для проекта установите Python 3.11–3.14.")
    if not 0 <= options.port <= 65535:
        parser.error("Порт должен быть целым числом от 0 до 65535.")
    try:
        interpreter = project_python()
        install_dependencies(interpreter)
        command = [
            interpreter,
            "-X",
            "utf8",
            "-m",
            "hotel.cli",
            "demo",
            "--port",
            str(options.port),
        ]
        if options.as_of:
            command.extend(["--as-of", options.as_of.isoformat()])
        if not options.no_browser:
            command.append("--open-browser")
        return subprocess.run(command, cwd=PROJECT_ROOT).returncode
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Не удалось подготовить запуск: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
