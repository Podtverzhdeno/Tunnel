"""CLI adapter for Local Workspace MCP run_script. Python stdlib only.

All filesystem paths are relative to the configured 'projects' workspace.
Read commands are harmless; service changes require an explicit --apply flag.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_LINES = 200
MAX_OUTPUT_BYTES = 32 * 1024
SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ToolError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def within_root(relative, root=None):
    """Resolve an existing path without allowing traversal or external symlinks."""
    root = ROOT if root is None else root
    if not relative or Path(relative).is_absolute():
        raise ToolError("PATH_INVALID", "Use a nonempty workspace-relative path")
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ToolError("PATH_OUTSIDE_WORKSPACE", "Path escapes the workspace")
    return path


def check_readable(relative, root=None):
    root = ROOT if root is None else root
    path = within_root(relative, root)
    if not path.is_file():
        raise ToolError("FILE_NOT_FOUND", "File is missing or not a regular file")
    parts = [part.lower() for part in path.relative_to(root.resolve()).parts]
    name = parts[-1]
    if (".git" in parts or ".ssh" in parts or name == ".env"
            or name.startswith(".env.") or name.endswith((".pem", ".key", ".p12", ".pfx"))
            or name in {"id_rsa", "id_ed25519", "credentials.json"}):
        raise ToolError("FILE_PROTECTED", "Protected secrets or private files are not readable")
    return path


def read_text(relative, start_line=1, max_lines=100, root=None):
    root = ROOT if root is None else root
    if not 1 <= start_line <= 100000000:
        raise ToolError("RANGE_INVALID", "start_line must be positive")
    if not 1 <= max_lines <= MAX_LINES:
        raise ToolError("RANGE_INVALID", f"max_lines must be 1..{MAX_LINES}")
    path = check_readable(relative, root)
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ToolError("FILE_TOO_LARGE", f"Maximum file size is {MAX_FILE_BYTES} bytes")
    data = path.read_bytes()
    if b"\x00" in data:
        raise ToolError("NOT_TEXT", "Binary file detected")
    try:
        contents = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ToolError("INVALID_UTF8", "File is not valid UTF-8")
    rows = contents.splitlines()
    selected = rows[start_line - 1:start_line - 1 + max_lines]
    output = []
    used = 0
    for index, value in enumerate(selected, start=start_line):
        count = len(value.encode("utf-8"))
        if count > MAX_OUTPUT_BYTES:
            raise ToolError("LINE_TOO_LONG", "A line exceeds the maximum response size")
        if count + used > MAX_OUTPUT_BYTES:
            break
        output.append({"number": index, "text": value})
        used += count
    return {
        "path": path.relative_to(root.resolve()).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encoding": "utf-8",
        "total_lines": len(rows),
        "start_line": start_line,
        "lines": output,
        "has_more": start_line - 1 + len(output) < len(rows),
        "truncated": len(output) < len(selected),
    }


def run_executable(executable, args, cwd=ROOT, timeout=15):
    binary = shutil.which(executable)
    if not binary:
        raise ToolError("TOOL_NOT_INSTALLED", f"{executable} is not in PATH")
    try:
        result = subprocess.run(
            [binary, *args], cwd=str(cwd), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError("TOOL_TIMEOUT", f"{executable} exceeded {timeout}s", retryable=True)
    except OSError:
        raise ToolError("TOOL_START_FAILED", f"Could not launch {executable}")
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[:12000],
        "stderr": result.stderr[:1500],
    }


def docker_status():
    return {"tool": "docker_status",
            "result": run_executable("docker", ["info", "--format", "{{json .ServerVersion}}"])}


def docker_ps():
    result = run_executable("docker", ["ps", "--format", "{{json .}}"])
    if result["ok"]:
        records = []
        for item in result["stdout"].splitlines()[:100]:
            try:
                records.append(json.loads(item))
            except ValueError:
                raise ToolError("DOCKER_OUTPUT_INVALID", "Docker returned malformed JSON")
        return {"tool": "docker_ps", "containers": records, "truncated": len(result["stdout"].splitlines()) > 100}
    return {"tool": "docker_ps", "result": result}


def postgres_ping(host="127.0.0.1", port=5432, user="postgres", database="postgres"):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ToolError("HOST_DENIED", "Only local PostgreSQL is permitted")
    if not 1 <= port <= 65535:
        raise ToolError("PORT_INVALID", "Invalid TCP port")
    for value in (user, database):
        if not SERVICE_PATTERN.fullmatch(value):
            raise ToolError("IDENTIFIER_INVALID", "Invalid user or database name")
    result = run_executable("pg_isready", [
        "-h", host, "-p", str(port), "-U", user, "-d", database,
        "-t", "5",
    ], timeout=10)
    return {"tool": "postgres_ping", "ready": result["ok"], "result": result}


def compose_plan(file, service, apply=False):
    path = check_readable(file)
    name = path.name.lower()
    if not (name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")):
        raise ToolError("COMPOSE_FILE_INVALID", "Only compose.yml/.yaml or docker-compose.yml/.yaml")
    if not SERVICE_PATTERN.fullmatch(service):
        raise ToolError("SERVICE_INVALID", "Invalid service name")
    relative = path.relative_to(ROOT.resolve()).as_posix()
    args = ["compose", "-f", str(path), "up", "-d", "--no-build", "--no-recreate", service]
    if not apply:
        return {"tool": "compose_up", "executed": False, "requires_approval": True,
                "service": service, "compose_file": relative, "preview": ["docker", *args]}
    result = run_executable("docker", args, cwd=path.parent, timeout=90)
    return {"tool": "compose_up", "executed": True, "service": service,
            "compose_file": relative, "result": result}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    r = sub.add_parser("read_text")
    r.add_argument("path")
    r.add_argument("--start-line", type=int, default=1)
    r.add_argument("--max-lines", type=int, default=100)
    sub.add_parser("docker_status")
    sub.add_parser("docker_ps")
    p = sub.add_parser("postgres_ping")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--user", default="postgres")
    p.add_argument("--database", default="postgres")
    c = sub.add_parser("compose_up")
    c.add_argument("file")
    c.add_argument("service")
    c.add_argument("--apply", action="store_true",
                   help="Actually launch the specified Compose service")
    return parser


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    from infra_tools import ACTIONS, cli
    from spec_tools import ACTIONS as SPEC_ACTIONS, cli as spec_cli
    if arguments and arguments[0] in SPEC_ACTIONS:
        return spec_cli(arguments)
    if arguments and arguments[0] in ACTIONS:
        return cli(arguments)
    try:
        args = build_parser().parse_args(arguments)
        if args.action == "read_text":
            result = read_text(args.path, args.start_line, args.max_lines)
        elif args.action == "docker_status":
            result = docker_status()
        elif args.action == "docker_ps":
            result = docker_ps()
        elif args.action == "postgres_ping":
            result = postgres_ping(args.host, args.port, args.user, args.database)
        else:
            result = compose_plan(args.file, args.service, args.apply)
        response, exit_code = {"ok": True, "data": result}, 0
    except ToolError as exc:
        response, exit_code = {
            "ok": False, "error": {"code": exc.code, "message": str(exc),
                                   "retryable": exc.retryable}}, 1
    except Exception:
        response, exit_code = {
            "ok": False, "error": {"code": "INTERNAL_ERROR",
                                   "message": "Unhandled tool failure", "retryable": False}}, 2
    # ASCII JSON avoids Windows console encodings and preserves Unicode when decoded.
    print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
