from __future__ import annotations

import os
import platform
import re
import selectors
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


MAX_BINARY_NAME_CHARS = 128
MAX_PACKAGE_NAME_CHARS = 128
MAX_PACKAGE_QUERY_CHARS = 160
MAX_PACKAGE_RESULTS = 50
MAX_QML_MODULE_CHARS = 200
MAX_COMMAND_STDOUT_BYTES = 64 * 1024
MAX_COMMAND_STDERR_BYTES = 8 * 1024
LOCAL_COMMAND_TIMEOUT_SECONDS = 5
REPOSITORY_COMMAND_TIMEOUT_SECONDS = 12

_BINARY_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
_PACKAGE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]*\Z")
_PACKAGE_QUERY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._ -]*\Z")
_QML_MODULE_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z"
)
_SAFE_ENV_LABEL_RE = re.compile(r"[A-Za-z0-9_.:+-]{1,80}\Z")
_RPM_QUERY_FORMAT = "%{NAME}\t%{EVR}\t%{ARCH}\t%{SUMMARY}\n"
_RPM_SEARCH_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9+._-]{0,127})\s+:\s+(?P<summary>.+)$"
)
_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])(?P<version>\d+(?:\.\d+){1,3})(?![A-Za-z0-9])")
_TRUSTED_EXECUTABLE_ROOTS = (
    Path("/bin"),
    Path("/sbin"),
    Path("/usr/bin"),
    Path("/usr/sbin"),
)
_SYSTEM_DATA_ROOTS = (
    Path("/lib"),
    Path("/lib64"),
    Path("/usr/lib"),
    Path("/usr/lib64"),
)
_STANDARD_QML_ROOTS = (
    Path("/usr/lib64/qt6/qml"),
    Path("/usr/lib/qt6/qml"),
    Path("/usr/lib/x86_64-linux-gnu/qt6/qml"),
    Path("/usr/lib64/qt5/qml"),
    Path("/usr/lib/qt5/qml"),
    Path("/usr/lib/x86_64-linux-gnu/qt5/qml"),
)


class SystemContextError(ValueError):
    """Контрольована помилка read-only межі перевірки хоста."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False
    executable_unavailable: bool = False


CommandRunner = Callable[[Sequence[str], int, int, int], CommandResult]
WhichFunction = Callable[[str], str | None]


def run_fixed_command(
    argv: Sequence[str],
    timeout: int,
    stdout_limit: int = MAX_COMMAND_STDOUT_BYTES,
    stderr_limit: int = MAX_COMMAND_STDERR_BYTES,
) -> CommandResult:
    """Виконати готовий argv без shell з обмеженням обох потоків."""

    try:
        process = subprocess.Popen(
            list(argv),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
    except OSError:
        return CommandResult(returncode=None, executable_unavailable=True)

    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout_limit))
    selector.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr_limit))
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    timed_out = False
    truncated = False

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            events = selector.select(min(remaining, 0.25))
            for key, _ in events:
                stream_name, stream_limit = key.data
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[stream_name]
                available = max(0, stream_limit - len(buffer))
                buffer.extend(chunk[:available])
                if len(chunk) > available:
                    truncated = True
                    process.kill()
                    break
            if truncated:
                break
    finally:
        selector.close()

    if timed_out or truncated:
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    process.stdout.close()
    process.stderr.close()

    return CommandResult(
        returncode=process.returncode,
        stdout=buffers["stdout"].decode("utf-8", errors="replace"),
        stderr=buffers["stderr"].decode("utf-8", errors="replace"),
        timed_out=timed_out,
        truncated=truncated,
    )


class SystemInspector:
    """Малий детермінований сервіс read-only перевірки хоста й пакунків."""

    def __init__(
        self,
        *,
        runner: CommandRunner = run_fixed_command,
        which: WhichFunction = shutil.which,
        environ: Mapping[str, str] | None = None,
        os_release_path: Path = Path("/etc/os-release"),
        ostree_booted_path: Path = Path("/run/ostree-booted"),
        qml_roots: Sequence[Path] | None = None,
    ) -> None:
        self._runner = runner
        self._which = which
        self._environ = os.environ if environ is None else environ
        self._os_release_path = os_release_path
        self._ostree_booted_path = ostree_booted_path
        self._qml_roots_override = tuple(qml_roots) if qml_roots is not None else None

    def system_info(self) -> dict[str, object]:
        os_release = self._read_os_release()
        plasma = self.plasma_info()
        return {
            "platform": platform.system() or None,
            "os_name": os_release.get("NAME"),
            "distribution_id": os_release.get("ID"),
            "distribution_version": os_release.get("VERSION_ID"),
            "distribution_variant": os_release.get("VARIANT"),
            "architecture": platform.machine() or None,
            "kernel_version": platform.release() or None,
            "atomic": self._ostree_booted_path.exists(),
            "desktop_environment": plasma["desktop_environment"],
            "session_type": plasma["session_type"],
            "plasma_version": plasma["plasma_version"],
            "qt_version": plasma["qt_version"],
            "qt_major": plasma["qt_major"],
            "package_backends": self._package_backend_descriptions(),
            "identity_fields_omitted": [
                "hostname",
                "username",
                "home",
                "network",
                "machine_id",
                "serial_numbers",
            ],
        }

    def binary_exists(self, name: str) -> dict[str, object]:
        normalized = _validate_binary_name(name)
        resolved = self._which(normalized)
        if not resolved:
            return {"name": normalized, "exists": False, "path": None, "path_scope": None}
        path = _resolved_path(resolved)
        is_system = path is not None and _is_trusted_executable_path(path)
        return {
            "name": normalized,
            "exists": True,
            "path": str(path) if is_system else None,
            "path_scope": "system" if is_system else "non_system_hidden",
        }

    def package_installed(self, name: str) -> dict[str, object]:
        normalized = _validate_package_name(name)
        rpm = self._trusted_executable("rpm")
        if rpm is None:
            raise SystemContextError("backend_unavailable", "База пакунків RPM недоступна.")
        result = self._runner(
            [str(rpm), "--query", "--queryformat", _RPM_QUERY_FORMAT, "--", normalized],
            LOCAL_COMMAND_TIMEOUT_SECONDS,
            MAX_COMMAND_STDOUT_BYTES,
            MAX_COMMAND_STDERR_BYTES,
        )
        self._raise_command_failure(result, executable="rpm", operation="Пошук пакунка RPM")
        if result.returncode != 0:
            if "not installed" in result.stderr.casefold() or "not installed" in result.stdout.casefold():
                return {
                    "name": normalized,
                    "installed": False,
                    "version": None,
                    "architecture": None,
                    "description": None,
                    "backend": "rpm",
                    "source": "host_rpm_database",
                }
            raise SystemContextError("parser_failure", "Пошук пакунка RPM повернув нерозпізнаний результат.")

        records = _parse_rpm_records(result.stdout)
        if not records:
            raise SystemContextError("parser_failure", "Не вдалося розібрати метадані пакунка RPM.")
        selected = next((item for item in records if item["name"] == normalized), records[0])
        return {
            "name": selected["name"],
            "installed": True,
            "version": selected["version"],
            "architecture": selected["architecture"],
            "description": selected["description"],
            "backend": "rpm",
            "source": "host_rpm_database",
        }

    def package_info(self, name: str) -> dict[str, object]:
        normalized = _validate_package_name(name)
        installed = self.package_installed(normalized)
        repository_error: dict[str, str] | None = None
        repository_matches: list[dict[str, object]] = []
        repository_truncated = False
        try:
            repository_matches, repository_truncated = self._search_cached_repositories(normalized)
        except SystemContextError as exc:
            repository_error = {"code": exc.code, "message": str(exc)}

        exact = next(
            (item for item in repository_matches if str(item["name"]).casefold() == normalized.casefold()),
            None,
        )
        if not installed["installed"] and exact is None:
            if repository_error is not None:
                raise SystemContextError(repository_error["code"], repository_error["message"])
            raise SystemContextError(
                "package_not_found",
                "Пакунок не знайдено серед установлених пакунків або в кешованих метаданих репозиторію.",
            )

        return {
            "name": normalized,
            "installed": installed["installed"],
            "installed_version": installed["version"],
            "available": bool(exact) if repository_error is None else None,
            "available_version": None,
            "architecture": installed["architecture"],
            "description": installed["description"] or (exact or {}).get("description"),
            "installed_backend": "rpm",
            "repository_backend": "rpm-ostree-cache" if repository_error is None else None,
            "repository": None,
            "partial": repository_error is not None or exact is not None,
            "repository_error": repository_error,
            "unsupported_fields": ["available_version", "repository", "available_architecture"],
            "truncated": repository_truncated,
        }

    def package_search(self, query: str, limit: int = 10) -> dict[str, object]:
        normalized = _validate_package_query(query)
        bounded_limit = _bounded_int(limit, default=10, minimum=1, maximum=MAX_PACKAGE_RESULTS)
        matches, command_truncated = self._search_cached_repositories(normalized)
        truncated = command_truncated or len(matches) > bounded_limit
        return {
            "query": normalized,
            "backend": "rpm-ostree-cache",
            "network_used": False,
            "metadata_mode": "cache_only",
            "limit": bounded_limit,
            "result_count": min(len(matches), bounded_limit),
            "truncated": truncated,
            "results": matches[:bounded_limit],
        }

    def plasma_info(self) -> dict[str, object]:
        desktop = _safe_environment_label(self._environ.get("XDG_CURRENT_DESKTOP"))
        session_type = _safe_session_type(self._environ.get("XDG_SESSION_TYPE"))
        plasma_version = _extract_version(self._optional_installed_version("plasma-workspace"))
        frameworks_version = _extract_version(self._optional_installed_version("kf6-kcoreaddons"))
        qt_version = self._qt_version()
        qt_major = _version_major(qt_version)
        plasma_major = _version_major(plasma_version)
        return {
            "desktop_environment": desktop,
            "session_type": session_type,
            "display_protocol": session_type if session_type in {"wayland", "x11"} else None,
            "plasma_version": plasma_version,
            "plasma_major": plasma_major,
            "kde_frameworks_version": frameworks_version,
            "qt_version": qt_version,
            "qt_major": qt_major,
            "inspection_binaries": {
                "qtpaths6": self._trusted_executable("qtpaths6") is not None,
                "rpm": self._trusted_executable("rpm") is not None,
            },
        }

    def qml_module_available(self, module: str) -> dict[str, object]:
        normalized = _validate_qml_module(module)
        qt_version = self._qt_version()
        roots = self._qml_import_roots()
        if not roots:
            raise SystemContextError(
                "executable_unavailable",
                "Немає доступної підтримуваної утиліти перевірки Qt QML або системного кореня імпорту.",
            )

        relative = Path(*normalized.split("."))
        for root in roots:
            qmldir = root / relative / "qmldir"
            try:
                if not qmldir.is_file():
                    continue
                resolved_root = root.resolve()
                resolved_qmldir = qmldir.resolve()
                if resolved_root not in resolved_qmldir.parents:
                    continue
                if resolved_qmldir.stat().st_size > 64 * 1024:
                    raise SystemContextError("parser_failure", "Метадані модуля QML перевищують ліміт перевірки.")
                content = resolved_qmldir.read_text(encoding="utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise SystemContextError("parser_failure", "Метадані модуля QML не є коректним UTF-8.") from exc
            except OSError as exc:
                raise SystemContextError("parser_failure", "Не вдалося прочитати метадані модуля QML.") from exc

            declared_uri, versions = _parse_qmldir(content)
            if declared_uri != normalized:
                raise SystemContextError("parser_failure", "Метадані QML містять неочікуваний ідентифікатор модуля.")
            provider = self._rpm_file_provider(resolved_qmldir)
            safe_location = (
                str(resolved_qmldir.parent)
                if _is_system_data_path(resolved_qmldir.parent)
                else None
            )
            return {
                "module": normalized,
                "available": True,
                "qt_major": _version_major(qt_version),
                "qt_version": qt_version,
                "module_version": None,
                "declared_type_versions": versions,
                "provider": provider,
                "location": safe_location,
                "metadata": "qmldir",
            }

        return {
            "module": normalized,
            "available": False,
            "qt_major": _version_major(qt_version),
            "qt_version": qt_version,
            "module_version": None,
            "declared_type_versions": [],
            "provider": None,
            "location": None,
            "metadata": "qmldir",
        }

    def _read_os_release(self) -> dict[str, str]:
        allowed = {"NAME", "ID", "VERSION_ID", "VARIANT"}
        try:
            if self._os_release_path.stat().st_size > 64 * 1024:
                return {}
            lines = self._os_release_path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeDecodeError):
            return {}
        result: dict[str, str] = {}
        for line in lines:
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, raw_value = line.split("=", 1)
            if key not in allowed:
                continue
            try:
                parsed = shlex.split(raw_value, posix=True)
            except ValueError:
                continue
            if len(parsed) == 1 and 0 < len(parsed[0]) <= 160:
                result[key] = parsed[0]
        return result

    def _package_backend_descriptions(self) -> list[dict[str, object]]:
        backends: list[dict[str, object]] = []
        if self._trusted_executable("rpm") is not None:
            backends.append(
                {
                    "name": "rpm",
                    "operations": ["installed_lookup", "installed_info", "file_provider_lookup"],
                    "network_possible": False,
                }
            )
        if self._repository_backend_available():
            backends.append(
                {
                    "name": "rpm-ostree-cache",
                    "operations": ["repository_search", "repository_presence", "repository_summary"],
                    "network_possible": False,
                    "metadata_mode": "cache_only",
                }
            )
        return backends

    def _repository_backend_available(self) -> bool:
        return self._ostree_booted_path.exists() and self._trusted_executable("rpm-ostree") is not None

    def _search_cached_repositories(self, query: str) -> tuple[list[dict[str, object]], bool]:
        if not self._repository_backend_available():
            raise SystemContextError(
                "backend_unavailable",
                "Немає доступного підтримуваного read-only backend для кешованих репозиторіїв.",
            )
        rpm_ostree = self._trusted_executable("rpm-ostree")
        if rpm_ostree is None:
            raise SystemContextError("backend_unavailable", "Backend rpm-ostree недоступний.")
        result = self._runner(
            [str(rpm_ostree), "search", "--cache-only", query],
            REPOSITORY_COMMAND_TIMEOUT_SECONDS,
            MAX_COMMAND_STDOUT_BYTES,
            MAX_COMMAND_STDERR_BYTES,
        )
        self._raise_command_failure(result, executable="rpm-ostree", operation="Пошук у кешованому репозиторії")
        matches = _parse_rpm_ostree_search(result.stdout)
        no_matches = "no matches found" in result.stdout.casefold()
        if result.returncode != 0 and not matches:
            details = f"{result.stdout}\n{result.stderr}".casefold()
            if "cache" in details or "metadata" in details or "repository" in details:
                raise SystemContextError(
                    "repository_metadata_unavailable",
                    "Кешовані метадані репозиторію недоступні.",
                )
            raise SystemContextError("parser_failure", "Пошук у кешованому репозиторії повернув нерозпізнаний результат.")
        if not matches and not no_matches and result.stdout.strip():
            raise SystemContextError("parser_failure", "Не вдалося розібрати результат пошуку в кешованому репозиторії.")
        return matches, result.truncated

    def _optional_installed_version(self, package_name: str) -> str | None:
        try:
            result = self.package_installed(package_name)
        except SystemContextError:
            return None
        return str(result["version"]) if result["installed"] and result["version"] else None

    def _qt_version(self) -> str | None:
        qtpaths = self._trusted_executable("qtpaths6")
        if qtpaths is None:
            return None
        result = self._runner(
            [str(qtpaths), "--qt-version"],
            LOCAL_COMMAND_TIMEOUT_SECONDS,
            1024,
            2048,
        )
        if result.timed_out or result.executable_unavailable or result.returncode != 0 or result.truncated:
            return None
        match = _VERSION_RE.search(result.stdout.strip())
        return match.group("version") if match else None

    def _qml_import_roots(self) -> tuple[Path, ...]:
        if self._qml_roots_override is not None:
            return tuple(path.resolve() for path in self._qml_roots_override if path.is_dir())
        roots: list[Path] = []
        qtpaths = self._trusted_executable("qtpaths6")
        if qtpaths is not None:
            result = self._runner(
                [str(qtpaths), "--query", "QT_INSTALL_QML"],
                LOCAL_COMMAND_TIMEOUT_SECONDS,
                4096,
                2048,
            )
            if result.returncode == 0 and not result.timed_out and not result.truncated:
                candidate = _resolved_path(result.stdout.strip())
                if candidate is not None and _is_system_data_path(candidate) and candidate.is_dir():
                    roots.append(candidate)
        for candidate in _STANDARD_QML_ROOTS:
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
        return tuple(roots)

    def _rpm_file_provider(self, path: Path) -> dict[str, str] | None:
        rpm = self._trusted_executable("rpm")
        if rpm is None:
            return None
        result = self._runner(
            [str(rpm), "--query", "--file", "--queryformat", _RPM_QUERY_FORMAT, "--", str(path)],
            LOCAL_COMMAND_TIMEOUT_SECONDS,
            MAX_COMMAND_STDOUT_BYTES,
            MAX_COMMAND_STDERR_BYTES,
        )
        if result.returncode != 0 or result.timed_out or result.executable_unavailable or result.truncated:
            return None
        records = _parse_rpm_records(result.stdout)
        if not records:
            return None
        record = records[0]
        return {
            "name": record["name"],
            "version": record["version"],
            "architecture": record["architecture"],
            "backend": "rpm",
        }

    def _trusted_executable(self, name: str) -> Path | None:
        resolved = _resolved_path(self._which(name) or "")
        return resolved if resolved is not None and _is_trusted_executable_path(resolved) else None

    @staticmethod
    def _raise_command_failure(result: CommandResult, *, executable: str, operation: str) -> None:
        if result.executable_unavailable:
            raise SystemContextError("executable_unavailable", f"Потрібний executable '{executable}' недоступний.")
        if result.timed_out:
            raise SystemContextError("timeout", f"Перевищено час очікування: {operation}.")
        if result.truncated and not result.stdout:
            raise SystemContextError("parser_failure", f"Операція перевищила ліміт виводу: {operation}.")


def _validate_binary_name(name: str) -> str:
    normalized = name.strip() if isinstance(name, str) else ""
    if not normalized or len(normalized) > MAX_BINARY_NAME_CHARS or not _BINARY_NAME_RE.fullmatch(normalized):
        raise SystemContextError("invalid_input", "Некоректна назва executable.")
    return normalized


def _validate_package_name(name: str) -> str:
    normalized = name.strip() if isinstance(name, str) else ""
    if not normalized or len(normalized) > MAX_PACKAGE_NAME_CHARS or not _PACKAGE_NAME_RE.fullmatch(normalized):
        raise SystemContextError("invalid_input", "Некоректна назва пакунка.")
    return normalized


def _validate_package_query(query: str) -> str:
    normalized = " ".join(query.split()) if isinstance(query, str) else ""
    if not normalized:
        raise SystemContextError("invalid_input", "Пошуковий запит пакунка не може бути порожнім.")
    if len(normalized) > MAX_PACKAGE_QUERY_CHARS:
        raise SystemContextError("invalid_input", "Пошуковий запит пакунка перевищує ліміт довжини.")
    if not _PACKAGE_QUERY_RE.fullmatch(normalized):
        raise SystemContextError("invalid_input", "Пошуковий запит пакунка містить непідтримувані символи.")
    return normalized


def _validate_qml_module(module: str) -> str:
    normalized = module.strip() if isinstance(module, str) else ""
    if not normalized or len(normalized) > MAX_QML_MODULE_CHARS or not _QML_MODULE_RE.fullmatch(normalized):
        raise SystemContextError("invalid_input", "Некоректний ідентифікатор модуля QML.")
    return normalized


def _parse_rpm_records(output: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\t", 3)
        if len(fields) != 4 or not _PACKAGE_NAME_RE.fullmatch(fields[0]):
            continue
        if not fields[1].strip() or not fields[2].strip():
            continue
        records.append(
            {
                "name": fields[0],
                "version": fields[1].strip()[:160],
                "architecture": fields[2].strip()[:40],
                "description": " ".join(fields[3].split())[:500] or "",
            }
        )
    return records


def _parse_rpm_ostree_search(output: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = _RPM_SEARCH_LINE_RE.fullmatch(line.strip())
        if not match:
            continue
        name = match.group("name")
        if name in seen:
            continue
        seen.add(name)
        results.append(
            {
                "name": name,
                "description": " ".join(match.group("summary").split())[:500],
                "version": None,
                "architecture": None,
                "repository": None,
            }
        )
    return results


def _parse_qmldir(content: str) -> tuple[str | None, list[str]]:
    declared_uri: str | None = None
    versions: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("module "):
            candidate = line.split(None, 1)[1].strip()
            declared_uri = candidate if _QML_MODULE_RE.fullmatch(candidate) else None
            continue
        for match in _VERSION_RE.finditer(line):
            versions.add(match.group("version"))
    return declared_uri, sorted(versions, key=lambda value: tuple(int(part) for part in value.split(".")))[:100]


def _safe_environment_label(value: str | None) -> str | None:
    if not value:
        return None
    parts = [part for part in value.split(":") if _SAFE_ENV_LABEL_RE.fullmatch(part)]
    return ":".join(parts)[:160] if parts else None


def _safe_session_type(value: str | None) -> str | None:
    normalized = (value or "").strip().casefold()
    return normalized if normalized in {"wayland", "x11", "tty", "mir"} else None


def _extract_version(value: str | None) -> str | None:
    match = _VERSION_RE.search(value or "")
    return match.group("version") if match else None


def _version_major(value: str | None) -> int | None:
    version = _extract_version(value)
    return int(version.split(".", 1)[0]) if version else None


def _resolved_path(value: str) -> Path | None:
    if not value or "\x00" in value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _is_trusted_executable_path(path: Path) -> bool:
    return any(path == root or root in path.parents for root in _TRUSTED_EXECUTABLE_ROOTS)


def _is_system_data_path(path: Path) -> bool:
    return any(path == root or root in path.parents for root in _SYSTEM_DATA_ROOTS)


def _bounded_int(value: int, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


_DEFAULT_INSPECTOR = SystemInspector()


def system_info() -> dict[str, object]:
    return _DEFAULT_INSPECTOR.system_info()


def binary_exists(name: str) -> dict[str, object]:
    return _DEFAULT_INSPECTOR.binary_exists(name)


def package_installed(name: str) -> dict[str, object]:
    return _DEFAULT_INSPECTOR.package_installed(name)


def package_info(name: str) -> dict[str, object]:
    return _DEFAULT_INSPECTOR.package_info(name)


def package_search(query: str, limit: int = 10) -> dict[str, object]:
    return _DEFAULT_INSPECTOR.package_search(query, limit)


def plasma_info() -> dict[str, object]:
    return _DEFAULT_INSPECTOR.plasma_info()


def qml_module_available(module: str) -> dict[str, object]:
    return _DEFAULT_INSPECTOR.qml_module_available(module)
