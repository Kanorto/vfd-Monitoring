import platform
import shutil
import sys
from pathlib import Path


APP_NAME = "VFD PC Monitor v1.1.0"
ENTRYPOINT = "vfd_monitor.py"
SUPPORTED_PYTHON = (3, 11)


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def ensure_supported_environment() -> None:
    if platform.system() != "Windows":
        fail("Сборка релизного .exe поддерживается только на Windows.")

    current = sys.version_info[:2]
    if current != SUPPORTED_PYTHON:
        fail(
            "Для стабильной сборки используйте Python "
            f"{SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}. "
            f"Текущая версия: {platform.python_version()}."
        )


def clean_previous_builds(root: Path) -> None:
    for directory in (root / "build", root / "dist"):
        if directory.exists():
            shutil.rmtree(directory)


def main() -> None:
    root = Path(__file__).resolve().parent
    ensure_supported_environment()
    clean_previous_builds(root)
    try:
        import PyInstaller.__main__
    except ModuleNotFoundError as exc:
        fail(
            "PyInstaller не установлен. Сначала выполните "
            "`python -m pip install -r requirements-build.txt`."
        )

    PyInstaller.__main__.run(
        [
            "--noconsole",
            "--onefile",
            "--clean",
            "--noupx",
            "--icon=NONE",
            f"--name={APP_NAME}",
            str(root / ENTRYPOINT),
        ]
    )


if __name__ == "__main__":
    main()
