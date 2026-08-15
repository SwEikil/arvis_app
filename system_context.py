from __future__ import annotations

import csv
import math
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

import config


MAX_BINARY_NAME_CHARS = 128
MAX_PACKAGE_NAME_CHARS = 128
MAX_PACKAGE_QUERY_CHARS = 160
MAX_PACKAGE_RESULTS = 50
MAX_QML_MODULE_CHARS = 200
MAX_COMMAND_STDOUT_BYTES = 64 * 1024
MAX_COMMAND_STDERR_BYTES = 8 * 1024
LOCAL_COMMAND_TIMEOUT_SECONDS = 5
REPOSITORY_COMMAND_TIMEOUT_SECONDS = 12
CPU_SAMPLE_INTERVAL_SECONDS = 0.1
MAX_METRICS_FILE_BYTES = 64 * 1024
NVIDIA_COMMAND_STDOUT_BYTES = 32 * 1024
NVIDIA_COMMAND_STDERR_BYTES = 4 * 1024

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
_NVIDIA_QUERY_FIELDS = (
    "name",
    "utilization.gpu",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "memory.total",
    "memory.used",
    "memory.free",
)
_NVIDIA_SMI_ARGS = (
    f"--query-gpu={','.join(_NVIDIA_QUERY_FIELDS)}",
    "--format=csv,noheader,nounits",
)
_CPU_HWMON_NAMES = {
    "coretemp",
    "k10temp",
    "zenpower",
    "cpu_thermal",
    "x86_pkg_temp",
}
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
SleepFunction = Callable[[float], None]
LoadAverageFunction = Callable[[], tuple[float, float, float]]
CpuCountFunction = Callable[[], int | None]
DiskUsageFunction = Callable[[str], object]


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
        proc_stat_path: Path = Path("/proc/stat"),
        proc_meminfo_path: Path = Path("/proc/meminfo"),
        proc_uptime_path: Path = Path("/proc/uptime"),
        hwmon_root: Path = Path("/sys/class/hwmon"),
        sleep: SleepFunction = time.sleep,
        getloadavg: LoadAverageFunction = os.getloadavg,
        cpu_count: CpuCountFunction = os.cpu_count,
        disk_usage: DiskUsageFunction = shutil.disk_usage,
        storage_path: str | os.PathLike[str] = "/",
        cpu_sample_interval: float = CPU_SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        self._runner = runner
        self._which = which
        self._environ = os.environ if environ is None else environ
        self._os_release_path = os_release_path
        self._ostree_booted_path = ostree_booted_path
        self._qml_roots_override = tuple(qml_roots) if qml_roots is not None else None
        self._proc_stat_path = proc_stat_path
        self._proc_meminfo_path = proc_meminfo_path
        self._proc_uptime_path = proc_uptime_path
        self._hwmon_root = hwmon_root
        self._sleep = sleep
        self._getloadavg = getloadavg
        self._cpu_count = cpu_count
        self._disk_usage = disk_usage
        try:
            self._storage_path: Path | None = Path(storage_path)
        except (TypeError, ValueError):
            self._storage_path = None
        self._cpu_sample_interval = min(max(float(cpu_sample_interval), 0.0), 1.0)

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

    def system_metrics(self) -> dict[str, object]:
        """Повернути один best-effort snapshot без приватної ідентифікації."""

        warnings: list[str] = []

        cpu_usage = self._cpu_usage_percent()
        cpu_usage_available = cpu_usage is not None
        if not cpu_usage_available:
            warnings.append("cpu_usage_unavailable")

        load = self._load_average()
        load_available = load is not None
        if not load_available:
            warnings.append("load_average_unavailable")

        temperature = self._cpu_temperature()
        temperature_available = temperature is not None
        if not temperature_available:
            warnings.append("cpu_temperature_unavailable")

        memory_result = self._memory_metrics()
        memory_available = memory_result is not None
        if memory_result is None:
            memory, swap = _empty_memory_metrics(), _empty_swap_metrics()
            warnings.append("memory_unavailable")
        else:
            memory, swap = memory_result

        uptime = self._uptime_seconds()
        uptime_available = uptime is not None
        if not uptime_available:
            warnings.append("uptime_unavailable")

        storage = self._root_storage_metrics()
        storage_available = storage is not None
        if storage is None:
            storage = _empty_storage_metrics()
            warnings.append("root_storage_unavailable")

        gpu, gpu_available, gpu_warning = self._nvidia_metrics()
        if gpu_warning is not None:
            warnings.append(gpu_warning)

        try:
            logical_cpus = self._cpu_count()
        except (OSError, RuntimeError, TypeError, ValueError):
            logical_cpus = None
        if not isinstance(logical_cpus, int) or logical_cpus <= 0:
            logical_cpus = None

        return {
            "cpu": {
                "usage_percent": cpu_usage,
                "logical_cpus": logical_cpus,
                "load": load or {"1m": None, "5m": None, "15m": None},
                "temperature_c": temperature,
            },
            "memory": memory,
            "swap": swap,
            "gpu": gpu,
            "storage": {"root": storage},
            "uptime_seconds": uptime,
            "backends": {
                "cpu_usage": _metric_backend(cpu_usage_available, "procfs"),
                "load_average": _metric_backend(load_available, "os"),
                "cpu_temperature": _metric_backend(temperature_available, "sysfs_hwmon"),
                "memory": _metric_backend(memory_available, "procfs"),
                "nvidia_gpu": _metric_backend(gpu_available, "nvidia-smi"),
                "root_storage": _metric_backend(storage_available, "statvfs"),
                "uptime": _metric_backend(uptime_available, "procfs"),
            },
            "warnings": warnings,
        }

    def _cpu_usage_percent(self) -> float | None:
        first_text = _read_bounded_text(self._proc_stat_path, MAX_METRICS_FILE_BYTES)
        first = _parse_cpu_stat(first_text or "")
        if first is None:
            return None
        try:
            self._sleep(self._cpu_sample_interval)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        second_text = _read_bounded_text(self._proc_stat_path, MAX_METRICS_FILE_BYTES)
        second = _parse_cpu_stat(second_text or "")
        return _calculate_cpu_usage_percent(first, second) if second is not None else None

    def _load_average(self) -> dict[str, float] | None:
        try:
            values = self._getloadavg()
            parsed = tuple(float(value) for value in values)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if len(parsed) != 3 or any(not math.isfinite(value) or value < 0 for value in parsed):
            return None
        return {"1m": round(parsed[0], 2), "5m": round(parsed[1], 2), "15m": round(parsed[2], 2)}

    def _memory_metrics(self) -> tuple[dict[str, int | float | None], dict[str, int | float | None]] | None:
        content = _read_bounded_text(self._proc_meminfo_path, MAX_METRICS_FILE_BYTES)
        values = _parse_meminfo(content or "")
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        free = values.get("MemFree")
        if total is None or total <= 0:
            return None
        if available is not None and available > total:
            available = None
        if free is not None and free > total:
            free = None
        used = max(0, total - available) if available is not None else None
        memory = {
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "free_bytes": free,
            "used_percent": _percentage(used, total),
        }

        swap_total = values.get("SwapTotal")
        swap_free = values.get("SwapFree")
        if swap_total is not None and swap_free is not None and swap_free > swap_total:
            swap_free = None
        swap_used = (
            max(0, swap_total - swap_free)
            if swap_total is not None and swap_free is not None
            else None
        )
        swap = {
            "total_bytes": swap_total,
            "used_bytes": swap_used,
            "free_bytes": swap_free,
            "used_percent": _percentage(swap_used, swap_total),
        }
        return memory, swap

    def _uptime_seconds(self) -> float | None:
        content = _read_bounded_text(self._proc_uptime_path, 4096)
        if not content:
            return None
        try:
            value = float(content.split()[0])
        except (IndexError, ValueError):
            return None
        return round(value, 2) if math.isfinite(value) and value >= 0 else None

    def _root_storage_metrics(self) -> dict[str, int | float | None] | None:
        if self._storage_path is None or not self._storage_path.is_absolute():
            return None
        try:
            usage = self._disk_usage(os.fspath(self._storage_path))
            total = int(getattr(usage, "total"))
            used = int(getattr(usage, "used"))
            free = int(getattr(usage, "free"))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None
        if min(total, used, free) < 0 or used > total or free > total:
            return None
        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "used_percent": _percentage(used, total),
        }

    def _cpu_temperature(self) -> float | None:
        try:
            hwmon_directories = sorted(self._hwmon_root.glob("hwmon*"))[:64]
        except (OSError, RuntimeError):
            return None

        candidates: list[tuple[int, float]] = []
        for directory in hwmon_directories:
            driver = (_read_bounded_text(directory / "name", 256) or "").strip().casefold()
            if driver not in _CPU_HWMON_NAMES:
                continue
            try:
                input_files = sorted(directory.glob("temp*_input"))[:128]
            except (OSError, RuntimeError):
                continue
            for input_path in input_files:
                temperature = _parse_millidegree_temperature(
                    _read_bounded_text(input_path, 256) or ""
                )
                if temperature is None:
                    continue
                label_path = input_path.with_name(input_path.name.replace("_input", "_label"))
                label = (_read_bounded_text(label_path, 256) or "").strip().casefold()
                candidates.append((_temperature_label_priority(label), temperature))

        if not candidates:
            return None
        best_priority = min(priority for priority, _ in candidates)
        return round(max(value for priority, value in candidates if priority == best_priority), 1)

    def _nvidia_metrics(self) -> tuple[list[dict[str, object]], bool, str | None]:
        try:
            executable = self._trusted_executable("nvidia-smi")
        except (OSError, RuntimeError, TypeError, ValueError):
            return [], False, "nvidia_smi_unavailable"
        if executable is None:
            return [], False, "nvidia_smi_unavailable"
        try:
            result = self._runner(
                [str(executable), *_NVIDIA_SMI_ARGS],
                LOCAL_COMMAND_TIMEOUT_SECONDS,
                NVIDIA_COMMAND_STDOUT_BYTES,
                NVIDIA_COMMAND_STDERR_BYTES,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return [], False, "nvidia_smi_failed"
        if (
            result.executable_unavailable
            or result.timed_out
            or result.truncated
            or result.returncode != 0
        ):
            return [], False, "nvidia_smi_failed"
        devices = _parse_nvidia_smi(result.stdout)
        if not devices:
            return [], False, "nvidia_smi_unavailable"
        partial = any(value is None for device in devices for value in device.values())
        return devices, True, "nvidia_metrics_partial" if partial else None

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


def _read_bounded_text(path: Path, max_bytes: int) -> str | None:
    try:
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
    except OSError:
        return None
    if len(content) > max_bytes:
        return None
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _parse_cpu_stat(content: str) -> tuple[int, int] | None:
    first_line = content.splitlines()[0] if content else ""
    fields = first_line.split()
    if len(fields) < 5 or fields[0] != "cpu":
        return None
    try:
        counters = [int(value) for value in fields[1:9]]
    except ValueError:
        return None
    if len(counters) < 4 or any(value < 0 for value in counters):
        return None
    counters.extend([0] * (8 - len(counters)))
    user, nice, system, idle, iowait, irq, softirq, steal = counters[:8]
    idle_total = idle + iowait
    non_idle = user + nice + system + irq + softirq + steal
    return idle_total + non_idle, idle_total


def _calculate_cpu_usage_percent(
    first: tuple[int, int], second: tuple[int, int]
) -> float | None:
    total_delta = second[0] - first[0]
    idle_delta = second[1] - first[1]
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        return None
    return round((total_delta - idle_delta) * 100.0 / total_delta, 1)


def _parse_meminfo(content: str) -> dict[str, int]:
    allowed = {"MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree"}
    values: dict[str, int] = {}
    for line in content.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        if key not in allowed:
            continue
        parts = raw_value.split()
        if len(parts) != 2 or parts[1] != "kB":
            continue
        try:
            kibibytes = int(parts[0])
        except ValueError:
            continue
        if 0 <= kibibytes <= (2**63 - 1) // 1024:
            values[key] = kibibytes * 1024
    return values


def _parse_millidegree_temperature(value: str) -> float | None:
    try:
        temperature = float(value.strip()) / 1000.0
    except ValueError:
        return None
    if not math.isfinite(temperature) or not -50.0 <= temperature <= 200.0:
        return None
    return temperature


def _temperature_label_priority(label: str) -> int:
    if "package" in label:
        return 0
    if label == "tctl":
        return 1
    if label == "tdie":
        return 2
    if "cpu" in label:
        return 3
    if not label:
        return 4
    return 5


def _parse_nvidia_smi(output: str) -> list[dict[str, object]]:
    devices: list[dict[str, object]] = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if len(row) != len(_NVIDIA_QUERY_FIELDS):
            continue
        model = _safe_hardware_label(row[0])
        utilization = _parse_bounded_float(row[1], minimum=0.0, maximum=100.0)
        temperature = _parse_bounded_float(row[2], minimum=-50.0, maximum=200.0)
        power = _parse_bounded_float(row[3], minimum=0.0)
        power_limit = _parse_bounded_float(row[4], minimum=0.0)
        vram_total = _parse_mebibytes(row[5])
        vram_used = _parse_mebibytes(row[6])
        vram_free = _parse_mebibytes(row[7])
        devices.append(
            {
                "vendor": "nvidia",
                "model": model,
                "utilization_percent": utilization,
                "temperature_c": temperature,
                "power_w": power,
                "power_limit_w": power_limit,
                "vram_total_bytes": vram_total,
                "vram_used_bytes": vram_used,
                "vram_free_bytes": vram_free,
                "vram_used_percent": _percentage(vram_used, vram_total),
            }
        )
    return devices


def _safe_hardware_label(value: str) -> str | None:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 160 or any(not character.isprintable() for character in normalized):
        return None
    return normalized


def _parse_bounded_float(
    value: str, *, minimum: float, maximum: float | None = None
) -> float | None:
    normalized = value.strip()
    if not normalized or normalized.casefold() in {
        "n/a",
        "[n/a]",
        "na",
        "not supported",
        "[not supported]",
        "-",
    }:
        return None
    try:
        parsed = float(normalized)
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed < minimum or (maximum is not None and parsed > maximum):
        return None
    return round(parsed, 2)


def _parse_mebibytes(value: str) -> int | None:
    parsed = _parse_bounded_float(value, minimum=0.0)
    return round(parsed * 1024 * 1024) if parsed is not None else None


def _percentage(used: int | None, total: int | None) -> float | None:
    if used is None or total is None or used < 0 or total < 0:
        return None
    if total == 0:
        return 0.0 if used == 0 else None
    return round(min(used * 100.0 / total, 100.0), 1)


def _metric_backend(available: bool, source: str) -> dict[str, object]:
    return {"available": available, "source": source}


def _empty_memory_metrics() -> dict[str, int | float | None]:
    return {
        "total_bytes": None,
        "used_bytes": None,
        "available_bytes": None,
        "free_bytes": None,
        "used_percent": None,
    }


def _empty_swap_metrics() -> dict[str, int | float | None]:
    return {
        "total_bytes": None,
        "used_bytes": None,
        "free_bytes": None,
        "used_percent": None,
    }


def _empty_storage_metrics() -> dict[str, int | float | None]:
    return {
        "total_bytes": None,
        "used_bytes": None,
        "free_bytes": None,
        "used_percent": None,
    }


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


def system_metrics() -> dict[str, object]:
    inspector = SystemInspector(storage_path=config.get_system_metrics_storage_path())
    return inspector.system_metrics()


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
