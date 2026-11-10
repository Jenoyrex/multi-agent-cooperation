"""The `cooperation-lab` command: start the app on this computer and open the browser.

Binds 127.0.0.1 unless --host says otherwise. Data lives in ~/.cooperation-lab (or $COOPERATION_LAB_HOME),
never in the package folder.
"""
from __future__ import annotations

import argparse
import errno
import os
import secrets
import threading
import webbrowser
from functools import partial
from pathlib import Path

from dashboard import demo

HOME_ENV = "COOPERATION_LAB_HOME"
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def data_dir(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(environ.get(HOME_ENV) or Path.home() / ".cooperation-lab").expanduser()


def use_data_dir(folder: Path) -> None:
    """Point every local store at the user's data folder."""
    from dashboard import sandbox as try_it
    from lab import evaluation
    from sandbox import keys
    from workbench import store

    folder.mkdir(parents=True, exist_ok=True)
    keys.env_file_dirs[:] = [folder]
    ensure_lab_secret(folder, evaluation)
    store.DB_PATH = folder / "workbench.db"
    try_it.SANDBOX_DIR = folder / "runs"
    evaluation.LAB_DIR = folder / "runs" / "lab"


def ensure_lab_secret(folder: Path, evaluation) -> None:
    """Agent Lab needs a persistent secret that keeps its hidden scenarios reproducible. A checkout sets one in .env; an
    installed app makes its own once, in the data folder. It is not an API key and is never sent to a model or an agent."""
    if demo.enabled() or os.environ.get(evaluation.SECRET_ENV, "").strip():
        return
    path = folder / "lab-secret"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    os.environ[evaluation.SECRET_ENV] = path.read_text(encoding="utf-8").strip()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cooperation-lab", description="Run Cooperation Lab on this computer.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1, this computer only)")
    parser.add_argument("--port", type=int, default=None, help=f"default {DEFAULT_PORT}, or any free port if that one is taken")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error(f"port {args.port} is out of range")
    return args


def bind(server_cls, handler, host: str, port) -> object:
    """A listening server: the requested port, or the default with a free port as fallback."""
    if port is not None:
        return server_cls((host, port), handler)
    try:
        return server_cls((host, DEFAULT_PORT), handler)
    except OSError as exc:
        if exc.errno not in (errno.EADDRINUSE, errno.EACCES, 10048, 10013):  # 10048, 10013: the Windows codes
            raise
        return server_cls((host, 0), handler)


def build_server(args: argparse.Namespace):
    from dashboard import server as S
    from dashboard.data import Store

    folder = data_dir()
    use_data_dir(folder)
    S.Handler.store = Store(folder / "results")  # the research results ship with a repository checkout, not with the app
    S.Handler.public_hosts = frozenset()
    return bind(S.LabServer, partial(S.Handler, directory=str(S.STATIC)), args.host, args.port), folder


def main(argv=None) -> None:
    args = parse_args(argv)
    httpd, folder = build_server(args)
    port = httpd.server_address[1]
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown}:{port}/"
    print(f"Cooperation Lab is running at {url}\nYour data folder: {folder}\nPress Ctrl+C to stop.", flush=True)
    if args.host not in LOOPBACK_HOSTS:
        print(f"WARNING: --host {args.host} makes this app reachable from other machines. API keys, model calls and Python "
              "agents still answer only to this computer, but saved experiments are readable by anyone who can reach it.", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, webbrowser.open, (url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
