from __future__ import annotations

import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from config import DEFAULT_MINECRAFT_SERVER_KEY
from config import DEFAULT_MINECRAFT_SERVER_NAME
from config import MinecraftServerConfig
from config import get_minecraft_server_config


TARGET_ALIASES = {
    "minecraft_server": DEFAULT_MINECRAFT_SERVER_KEY,
    "minecraft server": DEFAULT_MINECRAFT_SERVER_KEY,
    "майнкрафт сервер": DEFAULT_MINECRAFT_SERVER_KEY,
    "сервер майнкрафт": DEFAULT_MINECRAFT_SERVER_KEY,
    "майн сервер": DEFAULT_MINECRAFT_SERVER_KEY,
    "mc server": DEFAULT_MINECRAFT_SERVER_KEY,
}


MINECRAFT_ACTIONS = {
    "minecraft_server_status",
    "minecraft_server_start",
    "minecraft_server_stop",
    "minecraft_server_restart",
    "minecraft_server_logs",
    "minecraft_server_diagnostics",
    "minecraft_server_metrics",
}

READ_ONLY_ACTIONS = {
    "minecraft_server_status",
    "minecraft_server_logs",
    "minecraft_server_diagnostics",
    "minecraft_server_metrics",
}

IGNORED_NON_SERVER_PROCESSES = {
    "bash",
    "sh",
    "zsh",
    "fish",
    "python",
    "python3",
    "konsole",
    "tmux",
    "prismlauncher",
}

MINECRAFT_SERVER_MARKERS = {
    "minecraft",
    "server.jar",
    "forge",
    "neoforge",
    "fabric",
    "quilt",
    "libraries/net/minecraft",
    "cpw.mods.bootstraplauncher",
    "net.minecraftforge",
    "start-server.sh",
}

CLIENT_PROCESS_MARKERS = {
    "prismlauncher --launch",
    "prismrun --launch",
    "org.prismlauncher.entrypoint",
    "minecraft-1.20.1-client.jar",
    "newlaunch.jar",
    "--server ",
    "/app/bin/prismrun",
    "/usr/bin/bwrap --args",
}


@dataclass
class MinecraftServerActionResult:
    executed: bool
    status: str
    reason_code: str | None
    message: str
    details: str | None = None
    is_safety_block: bool = False


@dataclass
class ProcessCandidate:
    pid: int
    comm: str
    cwd: str
    cmdline_short: str
    match_strength: str
    match_reasons: list[str] = field(default_factory=list)
    classification: str = "weak_cwd"
    ppid: int | None = None
    cpu_percent: float | None = None
    memory_rss_kb: int | None = None
    memory_rss_mb: float | None = None
    memory_rss_gb: float | None = None
    uptime_seconds: float | None = None

    def to_details(self) -> list[str]:
        reasons = ", ".join(self.match_reasons) if self.match_reasons else "none"
        lines = [
            f"  - pid: {self.pid}",
            f"    ppid: {_format_optional(self.ppid)}",
            f"    comm: {self.comm}",
            f"    cwd: {self.cwd}",
            f"    cmdline_short: {self.cmdline_short}",
            f"    classification: {self.classification}",
            f"    match_strength: {self.match_strength}",
            f"    match_reasons: {reasons}",
            f"    cpu_percent: {_format_optional(self.cpu_percent)}",
            f"    memory_rss_kb: {_format_optional(self.memory_rss_kb)}",
            f"    memory_rss_mb: {_format_optional(self.memory_rss_mb)}",
            f"    memory_rss_gb: {_format_optional(self.memory_rss_gb)}",
            f"    uptime_seconds: {_format_optional(self.uptime_seconds)}",
        ]
        return lines


@dataclass
class ProcessScanResult:
    candidates: list[ProcessCandidate] = field(default_factory=list)

    @property
    def strong_candidates(self) -> list[ProcessCandidate]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.classification in {"managed_server", "unmanaged_server"} or candidate.match_strength == "strong"
        ]

    @property
    def weak_candidates(self) -> list[ProcessCandidate]:
        return [candidate for candidate in self.candidates if candidate.classification == "weak_cwd"]

    @property
    def managed_server_candidates(self) -> list[ProcessCandidate]:
        return [candidate for candidate in self.candidates if candidate.classification == "managed_server"]

    @property
    def unmanaged_server_candidates(self) -> list[ProcessCandidate]:
        return [candidate for candidate in self.candidates if candidate.classification == "unmanaged_server"]

    @property
    def ignored_client_candidates(self) -> list[ProcessCandidate]:
        return [candidate for candidate in self.candidates if candidate.classification == "ignored_client"]

    @property
    def server_candidates(self) -> list[ProcessCandidate]:
        return self.managed_server_candidates + self.unmanaged_server_candidates


@dataclass
class MinecraftServerState:
    running: bool
    managed_by_tmux: bool
    tmux_session_exists: bool
    unmanaged_java_process_found: bool
    strong_unmanaged_process_found: bool
    managed_server_process_found: bool
    unmanaged_server_process_found: bool
    duplicate_server_processes_detected: bool
    weak_process_candidates_found: bool
    ignored_client_processes_found: bool
    ignored_client_processes_count: int
    possible_unmanaged_process_found: bool
    control_available: bool
    cwd: str
    start_script_exists: bool
    start_command_configured: bool
    process_candidates: list[ProcessCandidate] = field(default_factory=list)

    def to_details(self) -> str:
        lines = [
            f"running: {self.running}",
            f"managed_by_tmux: {self.managed_by_tmux}",
            f"tmux_session_exists: {self.tmux_session_exists}",
            f"unmanaged_java_process_found: {self.unmanaged_java_process_found}",
            f"strong_unmanaged_process_found: {self.strong_unmanaged_process_found}",
            f"managed_server_process_found: {self.managed_server_process_found}",
            f"unmanaged_server_process_found: {self.unmanaged_server_process_found}",
            f"duplicate_server_processes_detected: {self.duplicate_server_processes_detected}",
            f"weak_process_candidates_found: {self.weak_process_candidates_found}",
            f"ignored_client_processes_found: {self.ignored_client_processes_found}",
            f"ignored_client_processes_count: {self.ignored_client_processes_count}",
            f"possible_unmanaged_process_found: {self.possible_unmanaged_process_found}",
            f"control_available: {self.control_available}",
            f"cwd: {self.cwd}",
            f"start_script_exists: {self.start_script_exists}",
            f"start_command_configured: {self.start_command_configured}",
            "process_candidates:",
        ]
        if self.process_candidates:
            for candidate in self.process_candidates:
                lines.extend(candidate.to_details())
        else:
            lines.append("  none")
        if self.running and not self.managed_by_tmux and self.unmanaged_java_process_found:
            lines.append(
                "note: Server must be stopped manually once, then started through Arvis to enable managed stop/restart."
            )
        elif self.possible_unmanaged_process_found:
            lines.append(
                "note: Found process candidates in server directory, but none look like a running Minecraft Java server."
            )
        if self.duplicate_server_processes_detected:
            lines.append("warning: Multiple Minecraft server Java processes detected.")
        return "\n".join(lines)


def normalize_minecraft_target(target: str | None) -> str:
    normalized = (target or "").strip().lower().replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    if not normalized:
        return DEFAULT_MINECRAFT_SERVER_KEY
    if normalized == "minecraft server":
        return DEFAULT_MINECRAFT_SERVER_KEY
    return TARGET_ALIASES.get(normalized, normalized.replace(" ", "_"))


def execute_minecraft_server_action(
    action: str,
    target: str | None,
    dry_run: bool = False,
) -> MinecraftServerActionResult:
    normalized_target = normalize_minecraft_target(target)
    server_config = _server_config()
    if server_config is None:
        return _not_configured(normalized_target)

    if normalized_target != server_config.key:
        return MinecraftServerActionResult(
            executed=False,
            status="unknown_target",
            reason_code="minecraft_server_target_not_whitelisted",
            message="Minecraft server target is not in the whitelist.",
            details=f"target={normalized_target}",
        )

    if action == "start_minecraft_server":
        action = "minecraft_server_start"

    if action == "minecraft_server_status":
        return _status()
    if action == "minecraft_server_diagnostics":
        return _diagnostics()
    if action == "minecraft_server_metrics":
        return _metrics()
    if action == "minecraft_server_logs":
        return _logs()
    if action == "minecraft_server_start":
        return _start(dry_run=dry_run)
    if action == "minecraft_server_stop":
        return _stop(dry_run=dry_run)
    if action == "minecraft_server_restart":
        return _restart(dry_run=dry_run)

    return MinecraftServerActionResult(
        executed=False,
        status="unknown_action",
        reason_code="minecraft_action_not_whitelisted",
        message="Minecraft server action is unknown or not whitelisted.",
        details=f"action={action}",
    )


def _status() -> MinecraftServerActionResult:
    state = _get_state()
    server_name = _server_name()
    if state.running and not state.managed_by_tmux and state.unmanaged_java_process_found:
        message = (
            f"{server_name} is running, but it is not managed by Arvis/tmux. "
            "Arvis will not start a duplicate. Stop/restart are unavailable until the server is restarted through Arvis."
        )
        status = "executed"
        reason_code = "minecraft_server_running"
    elif state.running:
        message = f"{server_name} is running."
        status = "executed"
        reason_code = "minecraft_server_running"
    elif state.possible_unmanaged_process_found:
        message = "Found process candidates in server directory, but none look like a running Minecraft Java server."
        status = "ambiguous"
        reason_code = "minecraft_process_detection_ambiguous"
    else:
        message = f"{server_name} is not running."
        status = "executed"
        reason_code = "minecraft_server_not_running"
    return MinecraftServerActionResult(
        executed=False,
        status=status,
        reason_code=reason_code,
        message=message,
        details=state.to_details(),
    )


def _diagnostics() -> MinecraftServerActionResult:
    state = _get_state()
    return MinecraftServerActionResult(
        executed=False,
        status="executed",
        reason_code="minecraft_server_diagnostics",
        message="Minecraft server diagnostics collected.",
        details=state.to_details(),
    )


def _metrics() -> MinecraftServerActionResult:
    state = _get_state()
    server_name = _server_name()
    server_processes = [
        candidate
        for candidate in state.process_candidates
        if candidate.classification in {"managed_server", "unmanaged_server"}
    ]
    if not state.running:
        message = f"{server_name} is not running. No server process metrics are available."
    elif server_processes:
        message = "Minecraft server metrics collected."
    else:
        message = f"{server_name} appears managed by tmux, but no Java server process metrics were found yet."

    return MinecraftServerActionResult(
        executed=False,
        status="executed",
        reason_code="minecraft_server_metrics",
        message=message,
        details=_metrics_details(state),
    )


def _start(dry_run: bool) -> MinecraftServerActionResult:
    state = _get_state()
    server_name = _server_name()
    if state.running:
        if not state.managed_by_tmux and state.unmanaged_java_process_found:
            return MinecraftServerActionResult(
                executed=False,
                status="already_running",
                reason_code="minecraft_server_already_running_unmanaged",
                message=(
                    f"{server_name} is already running outside Arvis/tmux. "
                    "Nothing was started to avoid duplicate server process."
                ),
                details=state.to_details(),
            )
        return MinecraftServerActionResult(
            executed=False,
            status="already_running",
            reason_code="minecraft_server_already_running",
            message=f"{server_name} is already running. Nothing was started.",
            details=state.to_details(),
        )

    config_error = _validate_start_config()
    if config_error is not None:
        return config_error

    command = _tmux_start_command()
    if dry_run:
        return MinecraftServerActionResult(
            executed=False,
            status="dry_run",
            reason_code="minecraft_server_start_dry_run",
            message=f"Dry-run: would start {server_name}.",
            details=_start_details(state, f"cwd={_server_dir()}; command={_format_command(command)}"),
        )

    result = _run(command)
    if result.returncode != 0:
        return MinecraftServerActionResult(
            executed=False,
            status="command_failed",
            reason_code="minecraft_server_start_failed",
            message="Minecraft server failed to start.",
            details=_start_details(state, _command_failure_details(command, result)),
        )

    return MinecraftServerActionResult(
        executed=True,
        status="executed",
        reason_code="minecraft_server_started",
        message=f"{server_name} start command launched.",
        details=_start_details(state, f"cwd={_server_dir()}; command={_format_command(command)}"),
    )


def _stop(dry_run: bool, timeout_seconds: int = 60) -> MinecraftServerActionResult:
    state = _get_state()
    server_name = _server_name()
    if not state.running:
        return MinecraftServerActionResult(
            executed=False,
            status="not_running",
            reason_code="minecraft_server_not_running",
            message=f"{server_name} is not running.",
            details=state.to_details(),
        )

    if not state.managed_by_tmux:
        return _unmanaged_result(state)

    command = ["tmux", "send-keys", "-t", _tmux_session(), "stop", "Enter"]
    if dry_run:
        return MinecraftServerActionResult(
            executed=False,
            status="dry_run",
            reason_code="minecraft_server_stop_dry_run",
            message=f"Dry-run: would send stop command to {server_name}.",
            details=f"command={_format_command(command)}",
        )

    result = _run(command)
    if result.returncode != 0:
        return MinecraftServerActionResult(
            executed=False,
            status="command_failed",
            reason_code="minecraft_server_stop_failed",
            message=f"Failed to send stop command to {server_name}.",
            details=_command_failure_details(command, result),
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _tmux_session_exists():
            return MinecraftServerActionResult(
                executed=True,
                status="executed",
                reason_code="minecraft_server_stopped",
                message=f"{server_name} stopped.",
                details=f"command={_format_command(command)}",
            )
        time.sleep(1)

    return MinecraftServerActionResult(
        executed=False,
        status="command_failed",
        reason_code="minecraft_server_stop_timeout",
        message="Stop command was sent, but server did not exit within timeout.",
        details=f"timeout_seconds={timeout_seconds}; command={_format_command(command)}",
    )


def _restart(dry_run: bool) -> MinecraftServerActionResult:
    state = _get_state()
    server_name = _server_name()
    if state.running and not state.managed_by_tmux:
        return _unmanaged_restart_result(state)

    if not state.running:
        start_result = _start(dry_run=dry_run)
        start_result.message = "Server was not running, starting it now."
        return start_result

    if dry_run:
        return MinecraftServerActionResult(
            executed=False,
            status="dry_run",
            reason_code="minecraft_server_restart_dry_run",
            message=f"Dry-run: would gracefully restart {server_name}.",
            details=state.to_details(),
        )

    stop_result = _stop(dry_run=False)
    if stop_result.status != "executed":
        return stop_result

    start_result = _start(dry_run=False)
    if start_result.status == "executed":
        start_result.reason_code = "minecraft_server_restarted"
        start_result.message = f"{server_name} restarted."
    return start_result


def _logs(line_count: int = 40) -> MinecraftServerActionResult:
    log_path = _server_dir() / "logs" / "latest.log"
    if not log_path.is_file():
        return MinecraftServerActionResult(
            executed=False,
            status="not_configured",
            reason_code="minecraft_log_not_found",
            message="Minecraft latest.log was not found.",
            details=f"path={log_path}",
        )

    lines: deque[str] = deque(maxlen=line_count)
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                lines.append(line.rstrip("\n"))
    except OSError as exc:
        return MinecraftServerActionResult(
            executed=False,
            status="command_failed",
            reason_code="minecraft_log_read_failed",
            message="Minecraft latest.log could not be read.",
            details=f"path={log_path}; error={exc}",
        )

    return MinecraftServerActionResult(
        executed=False,
        status="executed",
        reason_code="minecraft_logs_read",
        message=f"Last {min(line_count, len(lines))} Minecraft log lines.",
        details="\n".join(lines),
    )


def _metrics_details(state: MinecraftServerState) -> str:
    server_processes = [
        candidate
        for candidate in state.process_candidates
        if candidate.classification in {"managed_server", "unmanaged_server"}
    ]
    client_processes = [candidate for candidate in state.process_candidates if candidate.classification == "ignored_client"]
    server_pids = [str(candidate.pid) for candidate in server_processes]
    total_rss_kb = sum(candidate.memory_rss_kb or 0 for candidate in server_processes)
    total_rss_mb = round(total_rss_kb / 1024, 2)
    total_rss_gb = round(total_rss_mb / 1024, 3)
    total_cpu = round(sum(candidate.cpu_percent or 0.0 for candidate in server_processes), 2)
    lines = [
        f"running: {state.running}",
        f"managed_by_tmux: {state.managed_by_tmux}",
        f"server_pids: {', '.join(server_pids) if server_pids else 'none'}",
        f"cpu_percent: {total_cpu}",
        f"memory_rss_mb: {total_rss_mb}",
        f"memory_rss_gb: {total_rss_gb}",
        f"duplicate_server_processes_detected: {state.duplicate_server_processes_detected}",
        f"client_processes_detected: {len(client_processes)}",
        "server_processes:",
    ]
    if server_processes:
        for candidate in server_processes:
            lines.extend(candidate.to_details())
    else:
        lines.append("  none")

    lines.append("client_processes:")
    if client_processes:
        for candidate in client_processes:
            lines.extend(candidate.to_details())
    else:
        lines.append("  none")

    if state.duplicate_server_processes_detected:
        lines.append("warning: Multiple Minecraft server Java processes detected.")
    return "\n".join(lines)


def _get_state() -> MinecraftServerState:
    tmux_exists = _tmux_session_exists()
    scan = _scan_process_candidates(tmux_exists=tmux_exists)
    managed_found = bool(scan.managed_server_candidates)
    unmanaged_found = bool(scan.unmanaged_server_candidates)
    server_found = managed_found or unmanaged_found
    weak_found = bool(scan.weak_candidates)
    ignored_client_count = len(scan.ignored_client_candidates)
    duplicate_found = len(scan.server_candidates) > 1
    return MinecraftServerState(
        running=tmux_exists or server_found,
        managed_by_tmux=tmux_exists,
        tmux_session_exists=tmux_exists,
        unmanaged_java_process_found=unmanaged_found,
        strong_unmanaged_process_found=unmanaged_found,
        managed_server_process_found=managed_found,
        unmanaged_server_process_found=unmanaged_found,
        duplicate_server_processes_detected=duplicate_found,
        weak_process_candidates_found=weak_found,
        ignored_client_processes_found=ignored_client_count > 0,
        ignored_client_processes_count=ignored_client_count,
        possible_unmanaged_process_found=weak_found and not server_found,
        control_available=tmux_exists,
        cwd=str(_server_dir()),
        start_script_exists=_start_script_exists(),
        start_command_configured=bool(_start_command()),
        process_candidates=scan.candidates,
    )


def _validate_start_config() -> MinecraftServerActionResult | None:
    if not _server_dir().is_dir():
        return MinecraftServerActionResult(
            executed=False,
            status="not_configured",
            reason_code="minecraft_server_cwd_missing",
            message="Minecraft server directory is missing.",
            details=f"path={_server_dir()}",
        )

    script_path = _start_script()
    if script_path is not None and not script_path.is_file():
        return MinecraftServerActionResult(
            executed=False,
            status="not_configured",
            reason_code="minecraft_start_script_missing",
            message="Minecraft start script is missing.",
            details=f"path={script_path}",
        )

    if shutil.which("tmux") is None:
        return MinecraftServerActionResult(
            executed=False,
            status="not_configured",
            reason_code="tmux_missing",
            message="tmux is required to manage the Minecraft server safely.",
        )

    return None


def _unmanaged_result(state: MinecraftServerState) -> MinecraftServerActionResult:
    return MinecraftServerActionResult(
        executed=False,
        status="unsupported",
        reason_code="minecraft_server_unmanaged",
        message=(
            "Server is running outside Arvis/tmux. I cannot safely send the stop command. "
            "Stop it manually from its current console, then start it through Arvis."
        ),
        details=state.to_details(),
    )


def _unmanaged_restart_result(state: MinecraftServerState) -> MinecraftServerActionResult:
    return MinecraftServerActionResult(
        executed=False,
        status="unsupported",
        reason_code="minecraft_server_unmanaged_restart",
        message="Cannot restart unmanaged server safely. Stop it manually first, then start it through Arvis.",
        details=state.to_details(),
    )


def _tmux_session_exists() -> bool:
    result = _run(["tmux", "has-session", "-t", _tmux_session()])
    return result.returncode == 0


def _unmanaged_java_process_found() -> bool:
    return bool(_scan_process_candidates(tmux_exists=False).unmanaged_server_candidates)


def _scan_process_candidates(tmux_exists: bool = False) -> ProcessScanResult:
    proc_dir = Path("/proc")
    candidates: list[ProcessCandidate] = []
    if not proc_dir.exists():
        return ProcessScanResult(candidates)

    server_dir = _server_dir()
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        candidate = _process_candidate_from_proc_entry(entry, server_dir, tmux_exists)
        if candidate is not None:
            candidates.append(candidate)

    return ProcessScanResult(candidates)


def _process_candidate_from_proc_entry(entry: Path, server_dir: Path, tmux_exists: bool) -> ProcessCandidate | None:
    try:
        pid = int(entry.name)
    except ValueError:
        return None

    comm = _read_proc_text(entry / "comm").strip()
    cmdline = _read_proc_text(entry / "cmdline").replace("\x00", " ").strip()
    cmdline_short = _shorten(cmdline)
    is_client = _is_client_process(comm, cmdline)

    try:
        cwd = (entry / "cwd").resolve()
    except (FileNotFoundError, PermissionError, OSError):
        cwd_text = "(unknown)"
        cwd_inside_server_dir = False
    else:
        cwd_text = str(cwd)
        cwd_inside_server_dir = _is_relative_to(cwd, server_dir)

    if not cwd_inside_server_dir and not is_client:
        return None

    candidate = _classify_process_candidate(
        pid,
        comm,
        cwd_text,
        cmdline_short,
        cmdline,
        tmux_exists=tmux_exists,
        cwd_inside_server_dir=cwd_inside_server_dir,
    )
    return _with_process_metrics(candidate)


def _classify_process_candidate(
    pid: int,
    comm: str,
    cwd: str,
    cmdline_short: str,
    cmdline: str,
    tmux_exists: bool = False,
    cwd_inside_server_dir: bool = True,
) -> ProcessCandidate:
    reasons = ["cwd_inside_server_dir"] if cwd_inside_server_dir else ["client_process_marker"]
    normalized_comm = comm.lower()
    normalized_cmdline = cmdline.lower()
    has_java = "java" in normalized_comm or "java" in normalized_cmdline
    markers = _minecraft_markers(normalized_cmdline)
    client_markers = _client_markers(normalized_comm, normalized_cmdline)

    if has_java:
        reasons.append("java_process")
    if markers:
        reasons.extend(markers)
    if client_markers:
        reasons.extend(client_markers)
    if not normalized_cmdline:
        reasons.append("empty_cmdline")
    if normalized_comm in IGNORED_NON_SERVER_PROCESSES:
        reasons.append(f"ignored_process_name:{normalized_comm}")

    if client_markers:
        classification = "ignored_client"
    elif has_java and markers:
        classification = "managed_server" if tmux_exists else "unmanaged_server"
    else:
        classification = "weak_cwd"

    return ProcessCandidate(
        pid=pid,
        comm=comm or "(unknown)",
        cwd=cwd,
        cmdline_short=cmdline_short or "(empty)",
        match_strength="strong" if classification in {"managed_server", "unmanaged_server"} else "weak",
        match_reasons=reasons,
        classification=classification,
    )


def _minecraft_markers(normalized_cmdline: str) -> list[str]:
    return [f"marker:{marker}" for marker in sorted(MINECRAFT_SERVER_MARKERS) if marker in normalized_cmdline]


def _is_client_process(comm: str, cmdline: str) -> bool:
    return bool(_client_markers(comm.lower(), cmdline.lower()))


def _client_markers(normalized_comm: str, normalized_cmdline: str) -> list[str]:
    text = f"{normalized_comm} {normalized_cmdline}"
    return [f"client_marker:{marker}" for marker in sorted(CLIENT_PROCESS_MARKERS) if marker in text]


def _with_process_metrics(candidate: ProcessCandidate) -> ProcessCandidate:
    metrics = _collect_process_metrics(candidate.pid)
    if metrics is None:
        return candidate
    candidate.ppid = metrics.get("ppid")
    candidate.cpu_percent = metrics.get("cpu_percent")
    candidate.memory_rss_kb = metrics.get("memory_rss_kb")
    if candidate.memory_rss_kb is not None:
        candidate.memory_rss_mb = round(candidate.memory_rss_kb / 1024, 2)
        candidate.memory_rss_gb = round(candidate.memory_rss_mb / 1024, 3)
    candidate.uptime_seconds = _process_uptime_seconds(candidate.pid)
    return candidate


def _collect_process_metrics(pid: int) -> dict[str, int | float] | None:
    command = ["ps", "-p", str(pid), "-o", "pid=,ppid=,%cpu=,%mem=,rss=,comm=,args="]
    result = _run(command)
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    parts = lines[-1].split(maxsplit=6)
    if len(parts) < 5:
        return None
    try:
        return {
            "ppid": int(parts[1]),
            "cpu_percent": float(parts[2]),
            "memory_rss_kb": int(parts[4]),
        }
    except ValueError:
        return None


def _process_uptime_seconds(pid: int) -> float | None:
    stat_text = _read_proc_text(Path("/proc") / str(pid) / "stat")
    uptime_text = _read_proc_text(Path("/proc") / "uptime")
    if not stat_text or not uptime_text:
        return None
    try:
        start_ticks = int(stat_text.split()[21])
        system_uptime = float(uptime_text.split()[0])
        ticks_per_second = _clock_ticks_per_second()
    except (IndexError, ValueError):
        return None
    return round(max(0.0, system_uptime - (start_ticks / ticks_per_second)), 1)


def _clock_ticks_per_second() -> int:
    try:
        return int(subprocess.run(["getconf", "CLK_TCK"], shell=False, capture_output=True, text=True, timeout=5).stdout.strip())
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
        return 100


def _read_proc_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _shorten(value: str, max_length: int = 220) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3] + "..."


def _format_optional(value: object) -> str:
    return "unknown" if value is None else str(value)


def _start_details(state: MinecraftServerState, primary_details: str) -> str:
    if not state.possible_unmanaged_process_found:
        return primary_details
    return "\n".join(
        [
            primary_details,
            "warning: weak process candidates were found in the server directory, but they do not look like a running Minecraft Java server.",
            state.to_details(),
        ]
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _tmux_start_command() -> list[str]:
    return ["tmux", "new-session", "-d", "-s", _tmux_session(), "-c", str(_server_dir()), *_start_command()]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, shell=False, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, "", f"{command[0]} was not found.")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"{command[0]} timed out.")


def _command_failure_details(command: list[str], result: subprocess.CompletedProcess[str]) -> str:
    error_text = (result.stderr or result.stdout or "").strip()
    details = f"command={_format_command(command)}; returncode={result.returncode}"
    if error_text:
        details = f"{details}; error={error_text}"
    return details


def _server_dir() -> Path:
    config = _server_config()
    if config is None:
        return Path()
    return config.cwd


def _start_script() -> Path | None:
    command = _start_command()
    for part in command:
        if part.startswith("-"):
            continue
        if part.startswith(("/", "./", "../")) or "/" in part or part.endswith(".sh"):
            script_path = Path(part)
            if not script_path.is_absolute():
                script_path = _server_dir() / script_path
            return script_path
    return None


def _start_script_exists() -> bool:
    script_path = _start_script()
    return script_path is not None and script_path.is_file()


def _start_command() -> list[str]:
    config = _server_config()
    if config is None:
        return []
    return config.command


def _tmux_session() -> str:
    config = _server_config()
    if config is None:
        return f"arvis_minecraft_{DEFAULT_MINECRAFT_SERVER_KEY}"
    return config.tmux_session


def _server_config() -> MinecraftServerConfig | None:
    return get_minecraft_server_config()


def _server_name() -> str:
    config = _server_config()
    if config is None:
        return DEFAULT_MINECRAFT_SERVER_NAME
    return config.name


def _not_configured(target: str) -> MinecraftServerActionResult:
    return MinecraftServerActionResult(
        executed=False,
        status="not_configured",
        reason_code="minecraft_server_not_configured",
        message="Minecraft server is not configured yet.",
        details=f"target={target}",
    )


def _format_command(command: list[str]) -> str:
    return " ".join(command)
