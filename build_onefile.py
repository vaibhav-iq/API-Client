import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(cwd))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one-file Windows EXE using PyInstaller.")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use.")
    parser.add_argument("--app-name", default="api-client", help="Output executable name.")
    parser.add_argument(
        "--install-pyinstaller",
        action="store_true",
        help="Install/upgrade pyinstaller before build.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    entry = root / "postman_like_tester.pyw"
    if not entry.exists():
        raise SystemExit(f"Entry file not found: {entry}")

    python_exe = args.python

    if args.install_pyinstaller:
        run([python_exe, "-m", "pip", "install", "--upgrade", "pip"], root)
        run([python_exe, "-m", "pip", "install", "--upgrade", "pyinstaller"], root)

    run([python_exe, "-m", "PyInstaller", "--version"], root)

    icon_path = root / "assets" / "app.ico"
    if not icon_path.exists():
        run([python_exe, str(root / "make_icon.py")], root)

    release_dir = root / "release"
    build_dir = root / "build"
    release_dir.mkdir(parents=True, exist_ok=True)

    run(
        [
            python_exe,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--name",
            args.app_name,
            "--icon",
            str(icon_path),
            "--distpath",
            str(release_dir),
            "--workpath",
            str(build_dir),
            "--specpath",
            str(root),
            str(entry),
        ],
        root,
    )

    exe_path = release_dir / f"{args.app_name}.exe"
    print(f"Build complete. EXE: {exe_path}")


if __name__ == "__main__":
    main()
