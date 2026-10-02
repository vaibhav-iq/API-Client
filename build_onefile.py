import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(cwd))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def build(python_exe: str, root: Path, entry: Path, icon_path: Path, app_name: str, mode: str) -> Path:
    """Build one PyInstaller artifact for `mode` ('onefile' or 'onedir')."""
    dist_dir = root / "dist" / mode
    work_dir = root / "build" / mode
    dist_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        python_exe, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        f"--{mode}",          # --onefile or --onedir
        "--windowed",
        "--name", app_name,
        "--icon", str(icon_path),
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(root),
    ]
    docs_path = root / "docs"
    if docs_path.is_dir():
        # Bundle the Markdown guide so Help -> Documentation works from the build.
        cmd += ["--add-data", f"{docs_path}{os.pathsep}docs"]
    cmd.append(str(entry))
    run(cmd, root)

    if mode == "onefile":
        return dist_dir / f"{app_name}.exe"
    return dist_dir / app_name / f"{app_name}.exe"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Windows app with PyInstaller (one-file and/or one-dir).")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use.")
    parser.add_argument("--app-name", default="api-client", help="Output executable name.")
    parser.add_argument("--mode", choices=["onefile", "onedir", "both"], default="onefile",
                        help="Build a single EXE, a folder, or both.")
    parser.add_argument(
        "--install-pyinstaller",
        action="store_true",
        help="Install/upgrade pyinstaller before build.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    entry = root / "APIClient.pyw"
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

    modes = ["onefile", "onedir"] if args.mode == "both" else [args.mode]
    outputs = [build(python_exe, root, entry, icon_path, args.app_name, m) for m in modes]
    print("Build complete:")
    for path in outputs:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
