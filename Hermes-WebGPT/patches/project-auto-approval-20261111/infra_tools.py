"""Constrained local infrastructure operations for MCP run_script.

All mutating Compose actions are dry-run by default. SQL requires a restricted
database role; no shell execution or arbitrary remote endpoints.
"""
import argparse
import csv
import io
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_OUTPUT = 18000
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
COMPOSE_NAMES = ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")
REDACTIONS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b(\s*[=:]\s*|\s*:\s*)[\"']?[^\s,;\"']+"),
    re.compile(r"(?i)(postgres(?:ql)?://)[^@\s]+@"),
)


class InfraError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def redacted(text, limit=MAX_OUTPUT):
    result = text
    for pattern in REDACTIONS:
        if pattern.pattern.startswith("(?i)(postgres"):
            result = pattern.sub(r"\1***@", result)
        else:
            result = pattern.sub(r"\1\2***" if pattern.groups == 2 else r"\1***", result)
    return result[:limit]


def ensure_int(value, minimum, maximum, name):
    if not minimum <= value <= maximum:
        raise InfraError("INVALID_ARGUMENT", f"{name} must be between {minimum} and {maximum}")
    return value


def name_check(value, pattern, what):
    if not pattern.fullmatch(value):
        raise InfraError("INVALID_ARGUMENT", f"Invalid {what}")
    return value


def local_host(host):
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        raise InfraError("HOST_DENIED", "Only loopback addresses are permitted")
    if not parsed.is_loopback:
        raise InfraError("HOST_DENIED", "Only loopback addresses are permitted")
    return str(parsed)


def compose_file(relative):
    if not relative or Path(relative).is_absolute():
        raise InfraError("PATH_INVALID", "Compose path must be workspace-relative")
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise InfraError("PATH_OUTSIDE_WORKSPACE", "Compose file escapes the workspace")
    if not path.is_file() or path.name.lower() not in COMPOSE_NAMES:
        raise InfraError("COMPOSE_INVALID", "Expected an existing Compose YAML file")
    return path


def command(executable, args, timeout=15, cwd=None, env=None, max_output=MAX_OUTPUT):
    binary = shutil.which(executable)
    if not binary:
        raise InfraError("TOOL_MISSING", f"{executable} is not installed")
    try:
        cp = subprocess.run([binary, *args], cwd=str(cwd or ROOT),
                            capture_output=True, encoding="utf-8", errors="replace",
                            text=True, stdin=subprocess.DEVNULL,
                            timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired:
        raise InfraError("TIMEOUT", f"{executable} exceeded {timeout}s", retryable=True)
    except OSError as exc:
        raise InfraError("SPAWN_FAILED", f"Cannot execute {executable}: {type(exc).__name__}")
    return {"ok": cp.returncode == 0, "exit_code": cp.returncode,
            "stdout": redacted(cp.stdout, max_output),
            "stderr": redacted(cp.stderr, 2048),
            "truncated": len(cp.stdout) > max_output}


def require_ok(result, label):
    if not result["ok"]:
        raise InfraError("COMMAND_FAILED", f"{label} failed (exit {result['exit_code']}): {result['stderr'][:500]}")
    return result


def docker_logs(container, tail=100):
    name_check(container, CONTAINER, "container ID/name")
    ensure_int(tail, 1, 200, "tail")
    result = require_ok(command("docker", ["logs", "--timestamps", "--tail", str(tail), container], timeout=20), "docker logs")
    return {"container": container, "tail": tail, "result": result}


def docker_service_logs(file, service, tail=100):
    path = compose_file(file)
    name_check(service, SERVICE, "service")
    ensure_int(tail, 1, 200, "tail")
    result = command("docker", ["compose", "-f", str(path), "logs",
                                "--no-color", "--timestamps", "--tail", str(tail), service],
                     timeout=20, cwd=path.parent)
    require_ok(result, "docker compose logs")
    return {"service": service, "tail": tail, "result": result}


def docker_stats(container=None):
    args = ["stats", "--no-stream", "--format", "{{json .}}"]
    if container:
        name_check(container, CONTAINER, "container")
        args.append(container)
    result = command("docker", args, timeout=20)
    require_ok(result, "docker stats")
    rows = []
    for line in result["stdout"].splitlines()[:100]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            raise InfraError("DOCKER_OUTPUT_INVALID", "Invalid Docker stats response")
    return {"stats": rows, "truncated": result["truncated"] or len(rows) >= 100}


def db_args(host, port, user, database):
    host = local_host(host)
    ensure_int(port, 1, 65535, "port")
    name_check(user, IDENT, "PostgreSQL user")
    name_check(database, IDENT, "PostgreSQL database")
    return ["-h", host, "-p", str(port), "-U", user, "-d", database]


def pg_env():
    env = os.environ.copy()
    env["PGCONNECT_TIMEOUT"] = "5"
    env["PGCLIENTENCODING"] = "UTF8"
    # Enforced on the server side for this connection; SQL tool also wraps
    # statements in an explicit READ ONLY transaction.
    env["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1500"
    return env


def postgres_status(host="127.0.0.1", port=5432, user="postgres", database="postgres"):
    args = db_args(host, port, user, database)
    ready = command("pg_isready", args + ["-t", "5"], timeout=10)
    return {"ready": ready["ok"], "result": ready}


def psql(host, port, user, database, sql, timeout=12):
    args = db_args(host, port, user, database)
    # One --command string; psql does not process backslash meta-commands in -c.
    result = command("psql", ["-X", "-w", "-q", "--csv", "-t", "-v", "ON_ERROR_STOP=1",
                              *args, "-c", sql], env=pg_env(), timeout=timeout)
    require_ok(result, "psql")
    if result["truncated"]:
        raise InfraError("OUTPUT_TOO_LARGE", "PostgreSQL result exceeded the output limit")
    return list(csv.reader(io.StringIO(result["stdout"])))


def postgres_schema(host="127.0.0.1", port=5432, user="postgres",
                    database="postgres", schema="public", limit=100):
    name_check(schema, IDENT, "schema")
    ensure_int(limit, 1, 200, "limit")
    sql = ("SELECT table_schema,table_name,column_name,data_type,is_nullable "
           "FROM information_schema.columns WHERE table_schema = '"
           + schema + "' ORDER BY table_name,ordinal_position LIMIT " + str(limit))
    rows = psql(host, port, user, database, sql)
    return {"schema": schema, "columns": [
        {"schema": row[0], "table": row[1], "column": row[2],
         "type": row[3], "nullable": row[4] == "YES"}
        for row in rows if len(row) == 5
    ], "limit": limit}


SQL_DISALLOWED = re.compile(
    r"(?i)\b(insert|update|delete|merge|alter|drop|create|truncate|grant|revoke|"
    r"copy|call|execute|do|set|reset|commit|rollback|vacuum|analyze|"
    r"pg_sleep|pg_read_file|pg_write_file|pg_ls_dir|lo_export|"
    r"dblink_exec|pg_terminate_backend|pg_cancel_backend)\b"
)


def validate_select(query):
    value = query.strip()
    if len(value) > 4000 or not value:
        raise InfraError("SQL_INVALID", "SQL must contain 1..4000 characters")
    if not re.match(r"(?is)^select\s+", value):
        raise InfraError("SQL_DENIED", "Only SELECT statements are supported")
    if any(x in value for x in (";", "--", "/*", "*/", "$$", "\\", "\x00", "\r", "\n")):
        raise InfraError("SQL_DENIED", "SQL comments, metacommands and multiple statements are forbidden")
    if SQL_DISALLOWED.search(value):
        raise InfraError("SQL_DENIED", "SQL includes a prohibited operation or function")
    if re.search(r"(?i)\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", value):
        raise InfraError("SQL_DENIED", "Row locking is forbidden")
    # Avoid injected SQL string literals and expressions with side effects:
    # read-only DB role and transaction are essential additional protection.
    return value


def postgres_query_ro(query, host="127.0.0.1", port=5432,
                      user="mcp_readonly", database="postgres", limit=100):
    if user == "postgres":
        raise InfraError("ROLE_DENIED", "Use a dedicated unprivileged database role")
    name_check(user, IDENT, "database role")
    ensure_int(limit, 1, 200, "limit")
    query = validate_select(query)
    # Reject elevated roles before processing arbitrary SQL.
    check = ("SELECT CASE WHEN rolsuper OR rolcreatedb OR rolcreaterole OR "
             "rolreplication OR rolbypassrls OR "
             "pg_has_role(current_user,'pg_read_server_files','MEMBER') OR "
             "pg_has_role(current_user,'pg_write_server_files','MEMBER') OR "
             "pg_has_role(current_user,'pg_execute_server_program','MEMBER') "
             "THEN 'denied' ELSE 'allowed' END "
             "FROM pg_roles WHERE rolname=current_user")
    roles = psql(host, port, user, database, check)
    if roles != [["allowed"]]:
        raise InfraError("ROLE_DENIED", "Database role has elevated privileges")
    # No client-side concatenated semicolons are possible: validate_select rejects them.
    statement = ("BEGIN TRANSACTION READ ONLY; SET LOCAL statement_timeout='3000ms'; "
                 "SELECT row_to_json(mcp_ro) FROM (" + query + ") AS mcp_ro LIMIT " + str(limit)
                 + "; COMMIT;")
    raw_rows = psql(host, port, user, database, statement)
    try:
        rows = [json.loads(row[0]) for row in raw_rows if len(row) == 1]
    except (ValueError, TypeError):
        raise InfraError("OUTPUT_INVALID", "PostgreSQL returned invalid JSON rows")
    return {"row_limit": limit, "rows": rows, "format": "json_objects"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def health_url(value):
    target = urllib.parse.urlsplit(value)
    if target.scheme not in ("http", "https") or not target.hostname:
        raise InfraError("URL_INVALID", "Only absolute HTTP(S) URLs are supported")
    if target.username or target.password or target.fragment:
        raise InfraError("URL_DENIED", "Credentials and fragments in URLs are forbidden")
    local_host(target.hostname)
    if target.port is not None:
        ensure_int(target.port, 1, 65535, "port")
    return value


def http_healthcheck(url, timeout=5):
    health_url(url)
    ensure_int(timeout, 1, 10, "timeout")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "LocalWorkspaceHealth/1.0"})
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            response.read(256)
            return {"url": url, "healthy": 200 <= status < 300, "status": status}
    except urllib.error.HTTPError as exc:
        return {"url": url, "healthy": False, "status": exc.code,
                "error": "HTTP error or redirect"}
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        return {"url": url, "healthy": False,
                "error": type(exc).__name__}


def port_inspect(port):
    ensure_int(port, 1, 65535, "port")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        accepting = sock.connect_ex(("127.0.0.1", port)) == 0
    pid = None
    if os.name == "nt" and shutil.which("netstat"):
        data = command("netstat", ["-ano", "-p", "tcp"], timeout=8, max_output=500000)
        if data["ok"]:
            for line in data["stdout"].splitlines():
                columns = line.split()
                if (len(columns) >= 5 and columns[0].upper() == "TCP"
                        and columns[1].rsplit(":", 1)[-1] == str(port)
                        and columns[3].upper() == "LISTENING"):
                    try:
                        pid = int(columns[4])
                        break
                    except ValueError:
                        pass
    return {"host": "127.0.0.1", "port": port, "accepting": accepting, "pid": pid}


def compose_down(file, apply=False):
    path = compose_file(file)
    # Intentionally leave orphan containers and volumes untouched.
    args = ["compose", "-f", str(path), "down"]
    if not apply:
        return {"executed": False, "requires_approval": True,
                "preview": ["docker", *args], "compose_file": str(path.relative_to(ROOT))}
    result = command("docker", args, timeout=90, cwd=path.parent)
    return {"executed": True, "result": result, "volumes_removed": False}


def service_stack_up(file, service, health_url_value=None, postgres_check=False,
                     apply=False):
    path = compose_file(file)
    name_check(service, SERVICE, "service")
    if health_url_value is not None:
        health_url(health_url_value)
    args = ["compose", "-f", str(path), "up", "-d", "--no-build",
            "--no-recreate", service]
    plan = {"compose_file": str(path.relative_to(ROOT)),
            "service": service, "preview": ["docker", *args],
            "healthcheck": health_url_value, "postgres_check": postgres_check}
    if not apply:
        return {"executed": False, "requires_approval": True, **plan}
    result = command("docker", args, timeout=90, cwd=path.parent)
    checks = {}
    if result["ok"]:
        if postgres_check:
            checks["postgres"] = postgres_status()
        if health_url_value:
            checks["http"] = http_healthcheck(health_url_value)
    return {"executed": True, "result": result, "checks": checks, **plan}


def cli_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for op in ("docker_logs", "docker_stats", "docker_service_logs",
               "postgres_status", "postgres_schema", "postgres_query_ro",
               "http_healthcheck", "port_inspect", "compose_down", "service_stack_up"):
        p = sub.add_parser(op)
        if op == "docker_logs":
            p.add_argument("container"); p.add_argument("--tail", type=int, default=100)
        elif op == "docker_stats":
            p.add_argument("--container")
        elif op == "docker_service_logs":
            p.add_argument("file"); p.add_argument("service")
            p.add_argument("--tail", type=int, default=100)
        elif op.startswith("postgres_"):
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=5432)
            p.add_argument("--user", default="mcp_readonly" if op == "postgres_query_ro" else "postgres")
            p.add_argument("--database", default="postgres")
            if op == "postgres_schema":
                p.add_argument("--schema", default="public")
                p.add_argument("--limit", type=int, default=100)
            elif op == "postgres_query_ro":
                p.add_argument("query"); p.add_argument("--limit", type=int, default=100)
        elif op == "http_healthcheck":
            p.add_argument("url"); p.add_argument("--timeout", type=int, default=5)
        elif op == "port_inspect":
            p.add_argument("port", type=int)
        elif op == "compose_down":
            p.add_argument("file"); p.add_argument("--apply", action="store_true")
        elif op == "service_stack_up":
            p.add_argument("file"); p.add_argument("service")
            p.add_argument("--health-url"); p.add_argument("--postgres-check", action="store_true")
            p.add_argument("--apply", action="store_true")
    return parser


ACTIONS = {
    "docker_logs", "docker_stats", "docker_service_logs",
    "postgres_status", "postgres_schema", "postgres_query_ro",
    "http_healthcheck", "port_inspect", "compose_down", "service_stack_up",
}


def cli(argv):
    try:
        args = cli_parser().parse_args(argv)
        if args.action == "docker_logs":
            data = docker_logs(args.container, args.tail)
        elif args.action == "docker_service_logs":
            data = docker_service_logs(args.file, args.service, args.tail)
        elif args.action == "docker_stats":
            data = docker_stats(args.container)
        elif args.action == "postgres_status":
            data = postgres_status(args.host, args.port, args.user, args.database)
        elif args.action == "postgres_schema":
            data = postgres_schema(args.host, args.port, args.user, args.database, args.schema, args.limit)
        elif args.action == "postgres_query_ro":
            data = postgres_query_ro(args.query, args.host, args.port, args.user, args.database, args.limit)
        elif args.action == "http_healthcheck":
            data = http_healthcheck(args.url, args.timeout)
        elif args.action == "port_inspect":
            data = port_inspect(args.port)
        elif args.action == "compose_down":
            data = compose_down(args.file, args.apply)
        else:
            data = service_stack_up(args.file, args.service, args.health_url,
                                    args.postgres_check, args.apply)
        response = {"ok": True, "data": data}
        rc = 0
    except InfraError as exc:
        response = {"ok": False, "error": {"code": exc.code, "message": str(exc),
                                            "retryable": exc.retryable}}
        rc = 1
    except Exception:
        response = {"ok": False, "error": {"code": "INTERNAL_ERROR",
                                            "message": "Unhandled operation failure",
                                            "retryable": False}}
        rc = 2
    print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))
    return rc


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
