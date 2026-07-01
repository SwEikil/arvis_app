from __future__ import annotations

import shlex
import sys
from collections.abc import Callable
from dataclasses import replace

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.prompt import Prompt
from rich.table import Table

from command_router import CommandResult as RouterCommandResult
from command_router import CommandRouter
from command_router import should_try_intent_resolver
from doctor import DoctorOptions
from doctor import doctor_exit_code
from doctor import parse_doctor_args
from doctor import render_text_report
from doctor import run_cli as run_doctor_cli
from doctor import run_doctor
from intent_parser import parse_assistant_response
from intent_resolver import ALLOWED_ACTIONS
from intent_resolver import IntentResolver
from intent_resolver import ResolvedIntent
from intent_resolver import has_dangerous_text
from intent_resolver import looks_like_command
from intent_resolver import resolver_debug_warning
from intent_resolver import should_pass_to_router
from ollama_client import OllamaClient
from response_renderer import render_final_response
from runtime_state import RELOAD_STATE_FILE
from runtime_state import load_reload_state
from runtime_state import restart_current_process
from runtime_state import save_reload_state
from voice_config import load_voice_config
from voice_config import voice_disabled_message
from voice_ducking import VoiceDucking
from voice_text_normalizer import correct_voice_text
from voice_input import ensure_stt_model_loaded
from voice_input import get_voice_dependency_status
from voice_input import preflight_voice_capture
from voice_input import record_microphone_to_temp_wav
from voice_input import transcribe_recorded_audio


MAX_HISTORY_MESSAGES = 40
MAX_COMMAND_HISTORY = 10


console = Console()


def cli(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "doctor":
        return run_doctor_cli(args[1:])

    main()
    return 0


def main() -> None:
    client = OllamaClient()
    active_history: list[dict[str, str]] = []
    session_summary = ""
    debug = False
    router = CommandRouter(dry_run=True)
    resolver = IntentResolver(client)
    command_history: list[dict[str, object]] = []
    command_counter = 0

    reload_state_file_was_present = RELOAD_STATE_FILE.exists()
    reload_state = load_reload_state()
    reload_state_restored = False
    if reload_state is not None:
        session_summary, debug, command_counter, reload_state_restored = restore_runtime_state(
            reload_state,
            active_history,
            command_history,
            router,
        )

    show_startup(client)
    if reload_state_file_was_present:
        if reload_state_restored:
            console.print("[green]Reloaded successfully. Runtime state restored.[/green]")
        else:
            console.print("[green]Reloaded successfully.[/green]")

    while True:
        try:
            user_text = Prompt.ask("[bold cyan]Ти[/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Вихід.[/dim]")
            break

        if not user_text:
            continue

        command_result = handle_command(
            user_text,
            active_history,
            session_summary,
            debug,
            router,
            command_history,
            command_counter,
            lambda recognized_text: process_user_text(
                recognized_text,
                active_history,
                session_summary,
                debug,
                router,
                resolver,
                client,
                command_history,
                command_counter,
            ),
        )
        if command_result.exit_requested:
            break
        if command_result.handled:
            session_summary = command_result.session_summary
            debug = command_result.debug
            if command_result.command_counter is not None:
                command_counter = command_result.command_counter
            continue

        session_summary, command_counter = process_user_text(
            user_text,
            active_history,
            session_summary,
            debug,
            router,
            resolver,
            client,
            command_history,
            command_counter,
        )


class ReplCommandResult:
    def __init__(
        self,
        handled: bool,
        exit_requested: bool,
        session_summary: str,
        debug: bool,
        command_counter: int | None = None,
    ) -> None:
        self.handled = handled
        self.exit_requested = exit_requested
        self.session_summary = session_summary
        self.debug = debug
        self.command_counter = command_counter


def handle_command(
    user_text: str,
    active_history: list[dict[str, str]],
    session_summary: str,
    debug: bool,
    router: CommandRouter,
    command_history: list[dict[str, object]] | None = None,
    command_counter: int = 0,
    process_text: Callable[[str], tuple[str, int]] | None = None,
) -> ReplCommandResult:
    command = user_text.lower()

    if command in {"/exit", "/quit"}:
        console.print("[dim]Вихід.[/dim]")
        return ReplCommandResult(True, True, session_summary, debug)

    if command in {"/reload", "/restart"}:
        console.print("[cyan]Reloading Arvis, сер...[/cyan]")
        state_saved = save_reload_state(
            dry_run=router.dry_run,
            debug=debug,
            session_summary=session_summary,
            active_history=active_history,
            command_history=command_history or [],
            command_counter=command_counter,
        )
        if not state_saved:
            console.print("[yellow]Не вдалось зберегти runtime state. Перезапускаю без нього.[/yellow]")

        try:
            restart_current_process()
        except OSError as error:
            console.print(
                Panel(
                    f"{error}\n\nСпробуй вручну: /exit, потім python main.py",
                    title="RELOAD ERROR",
                    border_style="red",
                )
            )
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/doctor" or command.startswith("/doctor "):
        show_doctor_report(user_text)
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/actions":
        show_actions()
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/voice status":
        show_voice_status()
        return ReplCommandResult(True, False, session_summary, debug, command_counter)

    if command == "/voice warmup":
        handle_voice_warmup_command()
        return ReplCommandResult(True, False, session_summary, debug, command_counter)

    if command in {"/voice test", "/voice once", "/voice diagnose"}:
        updated_summary, updated_counter = handle_voice_capture_command(command, session_summary, command_counter, process_text)
        return ReplCommandResult(True, False, updated_summary, debug, updated_counter)

    if command == "/reset":
        active_history.clear()
        console.print("[green]Активну історію очищено.[/green]")
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/debug on":
        console.print("[green]Debug увімкнено.[/green]")
        return ReplCommandResult(True, False, session_summary, True)

    if command == "/debug off":
        console.print("[green]Debug вимкнено.[/green]")
        return ReplCommandResult(True, False, session_summary, False)

    if command == "/dryrun on":
        router.dry_run = True
        console.print("[green]Dry-run увімкнено.[/green]")
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/dryrun off":
        router.dry_run = False
        console.print("[yellow]Dry-run вимкнено. Safe whitelist actions можуть виконуватись.[/yellow]")
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/dryrun":
        state = "увімкнено" if router.dry_run else "вимкнено"
        console.print(f"[cyan]Dry-run: {state}[/cyan]")
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/history":
        show_history(active_history)
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/summary":
        show_summary(session_summary)
        return ReplCommandResult(True, False, session_summary, debug)

    if command == "/help":
        show_help()
        return ReplCommandResult(True, False, session_summary, debug)

    if command.startswith("/"):
        console.print("[yellow]Невідома команда. Введи /help.[/yellow]")
        return ReplCommandResult(True, False, session_summary, debug)

    return ReplCommandResult(False, False, session_summary, debug)


def process_user_text(
    user_text: str,
    active_history: list[dict[str, str]],
    session_summary: str,
    debug: bool,
    router: CommandRouter,
    resolver: IntentResolver,
    client: OllamaClient,
    command_history: list[dict[str, object]],
    command_counter: int,
) -> tuple[str, int]:
    active_history.append({"role": "user", "content": user_text})
    context_messages = build_context_messages(active_history, session_summary)

    with console.status("[bold green]Арвіс думає...[/bold green]", spinner="dots"):
        raw_response, error = client.chat(context_messages)

    if error:
        console.print(Panel(error, title="OLLAMA ERROR", border_style="red"))
        active_history.pop()
        return session_summary, command_counter

    parsed, warnings = parse_assistant_response(raw_response or "", debug=debug)
    router_results: list[RouterCommandResult] = []
    resolver_results: list[ResolvedIntent] = []
    resolver_clarification: ResolvedIntent | None = None
    final_router_result: RouterCommandResult | None = None
    final_resolver_result: ResolvedIntent | None = None
    final_action_intent = parsed.action_intent

    preferred_resolved = resolver.resolve(user_text, command_history)
    if should_pass_to_router(preferred_resolved):
        resolver_results.append(preferred_resolved)
        final_resolver_result = preferred_resolved
        resolved_intent = preferred_resolved.to_action_intent()
        final_action_intent = resolved_intent
        if resolved_intent is not None:
            router_result = router.route(resolved_intent, user_text=user_text)
            router_results.append(router_result)
            final_router_result = router_result
            command_counter = record_command_history(command_history, command_counter, user_text, router_result)
    elif parsed.action_intent is not None:
        router_result = router.route(parsed.action_intent, user_text=user_text)
        router_results.append(router_result)
        final_router_result = router_result
        should_repair = should_try_resolver_for_result(router_result, user_text)
        if not should_repair:
            command_counter = record_command_history(command_history, command_counter, user_text, router_result)

        if should_repair:
            resolved = resolver.resolve(user_text, command_history)
            resolver_results.append(resolved)
            final_resolver_result = resolved
            if should_pass_to_router(resolved):
                resolved_intent = resolved.to_action_intent()
                if resolved_intent is not None:
                    resolved_router_result = router.route(resolved_intent, user_text=user_text)
                    router_results.append(resolved_router_result)
                    final_router_result = resolved_router_result
                    command_counter = record_command_history(
                        command_history,
                        command_counter,
                        user_text,
                        resolved_router_result,
                    )
            else:
                resolver_clarification = resolved
    else:
        resolved = preferred_resolved
        if (
            resolved.action is not None
            or resolved.confidence >= 0.65
            or resolved.source == "context_repair"
            or looks_like_command(user_text)
        ):
            resolver_results.append(resolved)
            final_resolver_result = resolved
            if should_pass_to_router(resolved):
                resolved_intent = resolved.to_action_intent()
                if resolved_intent is not None:
                    router_result = router.route(resolved_intent, user_text=user_text)
                    router_results.append(router_result)
                    final_router_result = router_result
                    command_counter = record_command_history(command_history, command_counter, user_text, router_result)
            else:
                resolver_clarification = resolved

    final_response = render_final_response(
        parsed.message,
        final_router_result,
        resolver_result=final_resolver_result,
        debug=debug,
    )
    active_history.append(
        {
            "role": "assistant",
            "content": final_response if final_router_result is not None else parsed.message,
        }
    )
    session_summary = trim_history_with_summary_placeholder(active_history, session_summary)

    console.print(Panel(final_response, title="Арвіс", border_style="green"))

    if debug and final_router_result is not None and parsed.message.strip():
        console.print(Panel(parsed.message, title="RAW ASSISTANT MESSAGE", border_style="dim"))

    if final_action_intent is not None:
        show_action_intent(final_action_intent)

    if final_resolver_result is not None:
        show_intent_resolver(final_resolver_result)

    if final_router_result is not None:
        show_command_router(router, final_router_result)

    if resolver_clarification is not None:
        show_resolver_clarification(resolver_clarification)

    if parsed.memory_intent is not None:
        console.print(
            Panel(
                Pretty(_model_to_dict(parsed.memory_intent), expand_all=True),
                title="MEMORY INTENT",
                border_style="blue",
            )
        )

    if debug and warnings:
        console.print(Panel("\n".join(warnings), title="DEBUG", border_style="magenta"))

    return session_summary, command_counter


def build_context_messages(
    active_history: list[dict[str, str]],
    session_summary: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []

    if session_summary.strip():
        messages.append(
            {
                "role": "system",
                "content": f"Короткий підсумок попередньої розмови:\n{session_summary.strip()}",
            }
        )

    messages.extend(active_history[-MAX_HISTORY_MESSAGES:])
    return messages


def trim_history_with_summary_placeholder(
    active_history: list[dict[str, str]],
    session_summary: str,
) -> str:
    if len(active_history) <= MAX_HISTORY_MESSAGES:
        return session_summary

    overflow_count = len(active_history) - MAX_HISTORY_MESSAGES
    overflow_messages = active_history[:overflow_count]

    # Placeholder for a future rolling summarizer. For now it intentionally keeps
    # the existing summary unchanged and only trims active context.
    updated_summary = update_session_summary(session_summary, overflow_messages)
    del active_history[:overflow_count]
    return updated_summary


def update_session_summary(
    current_summary: str,
    old_messages: list[dict[str, str]],
) -> str:
    return current_summary


def restore_runtime_state(
    state: dict[str, object],
    active_history: list[dict[str, str]],
    command_history: list[dict[str, object]],
    router: CommandRouter,
) -> tuple[str, bool, int, bool]:
    session_summary = ""
    debug = False
    command_counter = 0
    restored = False

    dry_run = state.get("dry_run")
    if isinstance(dry_run, bool):
        router.dry_run = dry_run
        restored = True

    debug_value = state.get("debug")
    if isinstance(debug_value, bool):
        debug = debug_value
        restored = True

    summary_value = state.get("session_summary")
    if isinstance(summary_value, str):
        session_summary = summary_value
        restored = True

    restored_active_history = _valid_active_history(state.get("active_history"))
    if restored_active_history is not None:
        active_history[:] = restored_active_history[-MAX_HISTORY_MESSAGES:]
        restored = True

    restored_command_history = _valid_command_history(state.get("command_history"))
    if restored_command_history is not None:
        command_history[:] = restored_command_history[-MAX_COMMAND_HISTORY:]
        restored = True

    counter_value = state.get("command_counter")
    if isinstance(counter_value, int) and counter_value >= 0:
        command_counter = counter_value
        restored = True
    elif command_history:
        command_counter = max(
            (item.get("counter") for item in command_history if isinstance(item.get("counter"), int)),
            default=0,
        )

    return session_summary, debug, command_counter, restored


def _valid_active_history(value: object) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None

    messages: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        role = item.get("role")
        content = item.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            return None
        messages.append({"role": role, "content": content})
    return messages


def _valid_command_history(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, list):
        return None

    history: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        history.append(dict(item))
    return history


def show_startup(client: OllamaClient) -> None:
    console.print(
        Panel(
            f"[bold]Арвіс[/bold]\nМодель: [cyan]{client.model}[/cyan]\nOllama host: [cyan]{client.host}[/cyan]",
            title="Local AI Assistant",
            border_style="cyan",
        )
    )
    show_help()


def show_help() -> None:
    table = Table(title="Команди", box=box.SIMPLE)
    table.add_column("Команда", style="cyan", no_wrap=True)
    table.add_column("Дія")
    table.add_row("/exit або /quit", "Вийти")
    table.add_row("/reset", "Очистити активну історію")
    table.add_row("/debug on", "Увімкнути debug")
    table.add_row("/debug off", "Вимкнути debug")
    table.add_row("/dryrun", "Показати стан dry-run")
    table.add_row("/dryrun on", "Увімкнути dry-run")
    table.add_row("/dryrun off", "Вимкнути dry-run для safe whitelist actions")
    table.add_row("/reload або /restart", "Перезапустити Python-процес Арвіса")
    table.add_row("/doctor", "Перевірити локальну конфігурацію і готовність Арвіса")
    table.add_row("/actions", "Показати підтримувані desktop actions")
    table.add_row("/voice status", "Показати статус голосового режиму")
    table.add_row("/voice warmup", "Завантажити STT model без запису голосу")
    table.add_row("/voice diagnose", "Розпізнати голос і показати correction/resolver diagnostics")
    table.add_row("/voice test", "Записати тест голосу без виконання команди")
    table.add_row("/voice once", "Записати одну голосову команду і обробити як текст")
    table.add_row("/history", "Показати активну історію")
    table.add_row("/summary", "Показати session_summary")
    table.add_row("/help", "Показати команди")
    console.print(table)


def show_history(active_history: list[dict[str, str]]) -> None:
    if not active_history:
        console.print("[dim]Активна історія порожня.[/dim]")
        return

    table = Table(title=f"Активна історія ({len(active_history)}/{MAX_HISTORY_MESSAGES})", box=box.SIMPLE)
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Role", style="cyan", width=10)
    table.add_column("Content")

    for index, message in enumerate(active_history, start=1):
        content = message["content"].replace("\n", " ")
        if len(content) > 120:
            content = content[:117] + "..."
        table.add_row(str(index), message["role"], content)

    console.print(table)


def show_summary(session_summary: str) -> None:
    text = session_summary.strip() or "(session_summary поки порожній)"
    console.print(Panel(text, title="SESSION SUMMARY", border_style="blue"))


def show_doctor_report(user_text: str) -> None:
    try:
        parts = shlex.split(user_text)
    except ValueError as error:
        console.print(Panel(str(error), title="DOCTOR ERROR", border_style="red"))
        return

    args = parts[1:]
    try:
        options = parse_doctor_args(args)
    except SystemExit:
        console.print("[yellow]Невірні аргументи /doctor. Спробуй /doctor або /doctor --verbose.[/yellow]")
        return

    if options.json_output:
        console.print("[yellow]/doctor --json доступний у CLI: python main.py doctor --json[/yellow]")
        options = DoctorOptions(verbose=options.verbose, strict=options.strict, fix=options.fix, no_color=options.no_color)

    try:
        checks = run_doctor(options)
    except Exception as error:
        console.print(Panel(f"{type(error).__name__}: {error}", title="DOCTOR ERROR", border_style="red"))
        return

    console.print(render_text_report(checks, options))
    if doctor_exit_code(checks, options) != 0:
        console.print("[yellow]Doctor знайшов проблеми, сер.[/yellow]")


def show_actions() -> None:
    table = Table(title="Підтримувані desktop actions", box=box.SIMPLE)
    table.add_column("Action", style="cyan", no_wrap=True)
    table.add_column("Targets/examples")
    table.add_column("Status")
    rows = [
        ("open_app", "spotify, steam, brave, discord, telegram, youtube, google, github, chatgpt", "ready"),
        ("browser_task_run", "humanbenchmark_aim", "experimental"),
        ("music_pause", "media", "ready"),
        ("music_next", "media", "ready"),
        ("music_previous", "media", "ready"),
        ("media_seek_forward", "media", "ready"),
        ("media_seek_backward", "media", "ready"),
        ("music_repeat_track", "media", "ready"),
        ("music_shuffle_toggle", "media", "ready"),
        ("music_like_current", "media", "unsupported: Spotify API required"),
        ("volume_up", "system", "ready"),
        ("volume_down", "system", "ready"),
        ("volume_mute", "system", "ready"),
        ("volume_unmute", "system", "ready"),
        ("media_status", "media", "ready"),
        ("volume_status", "system", "ready"),
        ("volume_set", "system", "ready"),
        ("minecraft_server_status", "default", "ready if configured"),
    ]
    for action, targets, status in rows:
        table.add_row(action, targets, status)
    console.print(table)


def show_voice_status() -> None:
    config = load_voice_config()
    dependencies = get_voice_dependency_status()
    table = Table(title="Voice status", box=box.SIMPLE)
    table.add_column("Поле", style="cyan", no_wrap=True)
    table.add_column("Значення")
    table.add_row("enabled", "true" if config.enabled else "false")
    table.add_row("stt backend", config.stt_backend)
    table.add_row("stt model", config.stt_model)
    table.add_row("stt device", config.stt_device)
    table.add_row("compute type", config.stt_compute_type)
    table.add_row("mic device", config.mic_device or "(default input)")
    table.add_row("record seconds", str(config.record_seconds))
    table.add_row("language", config.language)
    table.add_row("allowed languages", ", ".join(config.allowed_languages))
    table.add_row("min rms", str(config.min_rms))
    table.add_row("min peak", str(config.min_peak))
    table.add_row("debug save last", "enabled" if config.debug_save_last else "disabled")
    table.add_row("ducking", "enabled" if config.ducking_enabled else "disabled")
    table.add_row("duck percent", str(config.duck_percent))
    table.add_row("duck restore", "enabled" if config.duck_restore else "disabled")
    table.add_row("faster-whisper", "available" if dependencies.faster_whisper_available else "missing")
    table.add_row("sounddevice", "available" if dependencies.sounddevice_available else "missing")
    table.add_row("numpy", "available" if dependencies.numpy_available else "missing")
    console.print(table)
    if not config.enabled:
        console.print(f"[yellow]{voice_disabled_message()}[/yellow]")


def handle_voice_warmup_command() -> None:
    config = load_voice_config()
    preflight = preflight_voice_capture(config)
    if preflight is not None:
        if preflight.no_speech:
            console.print("[yellow]Не почув команди, сер.[/yellow]")
        else:
            console.print(f"[red]{_voice_error_message(preflight.error)}[/red]")
        return

    console.print("[cyan]Готую STT model, сер. Перший запуск може зайняти кілька хвилин.[/cyan]")
    try:
        ensure_stt_model_loaded(config)
    except KeyboardInterrupt:
        console.print("[yellow]Голосову команду перервано, сер.[/yellow]")
        return
    except Exception as error:
        console.print(f"[red]Не зміг підготувати STT model, сер: {str(error).replace(chr(10), ' ')[:240]}[/red]")
        return
    console.print("[green]STT model готова, сер.[/green]")


def handle_voice_capture_command(
    command: str,
    session_summary: str,
    command_counter: int,
    process_text: Callable[[str], tuple[str, int]] | None,
) -> tuple[str, int]:
    config = load_voice_config()
    if command == "/voice diagnose":
        config = replace(config, debug_save_last=True)

    preflight = preflight_voice_capture(config)
    if preflight is not None:
        if preflight.no_speech:
            console.print("[yellow]Не почув команди, сер.[/yellow]")
        else:
            console.print(f"[red]{_voice_error_message(preflight.error)}[/red]")
        return session_summary, command_counter

    try:
        ensure_stt_model_loaded(config)
    except KeyboardInterrupt:
        console.print("[yellow]Голосову команду перервано, сер.[/yellow]")
        return session_summary, command_counter
    except Exception as error:
        console.print(f"[red]Не зміг підготувати STT model, сер: {str(error).replace(chr(10), ' ')[:240]}[/red]")
        return session_summary, command_counter

    temp_path = None
    ducking = VoiceDucking(config, warn=lambda message: console.print(f"[yellow]{message}[/yellow]"))
    try:
        with ducking:
            if ducking.applied:
                console.print("[cyan]Приглушив звук і слухаю, сер...[/cyan]")
            else:
                console.print("[cyan]Слухаю, сер...[/cyan]")
            temp_path = record_microphone_to_temp_wav(config)
    except KeyboardInterrupt:
        console.print("[yellow]Голосову команду перервано, сер.[/yellow]")
        return session_summary, command_counter
    except Exception as error:
        console.print(f"[red]Не зміг розпізнати голос, сер: {str(error).replace(chr(10), ' ')[:240]}[/red]")
        return session_summary, command_counter

    if ducking.restored:
        console.print("[green]Повернув гучність назад, сер.[/green]")

    try:
        result = transcribe_recorded_audio(temp_path, config)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    if result.debug_audio_path:
        console.print(f"[cyan]Debug audio збережено: {result.debug_audio_path}[/cyan]")

    if result.no_speech:
        console.print("[yellow]Не почув команди, сер.[/yellow]")
        return session_summary, command_counter
    if not result.ok:
        console.print(f"[red]{_voice_error_message(result.error)}[/red]")
        return session_summary, command_counter

    recognized_text = result.text.strip()
    if not recognized_text:
        console.print("[yellow]Не почув команди, сер.[/yellow]")
        return session_summary, command_counter

    console.print(f"[green]Розпізнав: {recognized_text}[/green]")
    if command == "/voice diagnose":
        show_voice_diagnose_result(recognized_text)
        return session_summary, command_counter

    if command == "/voice test":
        return session_summary, command_counter

    if not _should_route_voice_transcript(recognized_text):
        console.print("[yellow]Відкинув розпізнаний текст, бо мова/якість схожа на шум, сер.[/yellow]")
        return session_summary, command_counter

    if process_text is None:
        console.print("[red]Не зміг передати голосову команду в text pipeline, сер.[/red]")
        return session_summary, command_counter
    return process_text(recognized_text)


def _should_route_voice_transcript(text: str) -> bool:
    normalized = " ".join(text.strip().lower().replace("?", " ").replace("!", " ").replace(",", " ").split())
    diagnostic_phrases = ("ти мене чуєш", "чуєш мене", "мене чуєш")
    if any(phrase in normalized for phrase in diagnostic_phrases):
        return False

    resolved = IntentResolver().resolve(text, use_llm=False)
    if should_pass_to_router(resolved):
        return True

    correction = correct_voice_text(text)
    return looks_like_command(correction.corrected_text)


def show_voice_diagnose_result(recognized_text: str) -> None:
    correction = correct_voice_text(recognized_text)
    resolved = IntentResolver().resolve(recognized_text, use_llm=False)
    table = Table(title="Voice diagnose", box=box.SIMPLE)
    table.add_column("Поле", style="cyan", no_wrap=True)
    table.add_column("Значення")
    table.add_row("recognized_text", recognized_text)
    table.add_row("corrected_text", correction.corrected_text)
    table.add_row("corrections", "; ".join(correction.applied_corrections) or "")
    table.add_row("action", resolved.action or "")
    table.add_row("target", resolved.target or "")
    table.add_row("confidence", f"{resolved.confidence:.2f}")
    table.add_row("risk", resolved.risk)
    table.add_row("pass_to_router", str(should_pass_to_router(resolved)))
    console.print(table)


def _voice_error_message(error: str) -> str:
    if error == "unsafe_audio_device":
        return "Цей audio device схожий на monitor/output source, сер. Я не буду слухати звук системи."
    if error == "voice_disabled":
        return voice_disabled_message()
    if error == "unclear_voice":
        return "Відкинув розпізнаний текст, бо мова/якість схожа на шум, сер."
    safe_error = (error or "unknown error").replace("\n", " ")[:240]
    return f"Не зміг розпізнати голос, сер: {safe_error}"


def show_command_router(router: CommandRouter, result: RouterCommandResult) -> None:
    table = Table(box=box.SIMPLE)
    table.add_column("Поле", style="cyan", no_wrap=True)
    table.add_column("Значення")
    table.add_row("dry-run", "on" if router.dry_run else "off")
    table.add_row("executed", str(result.executed))
    table.add_row("status", result.status)
    table.add_row("reason_code", result.reason_code or "")
    table.add_row("is_safety_block", str(result.is_safety_block))
    table.add_row("original action", result.original_action or "")
    table.add_row("normalized action", result.normalized_action or result.action)
    table.add_row("original target", result.original_target or "")
    table.add_row("normalized target", result.normalized_target or "")
    if result.original_user_text:
        table.add_row("user text", result.original_user_text)
    if result.params:
        table.add_row("params", str(result.params))
    table.add_row("message", result.message)
    if result.details:
        table.add_row("details", result.details)

    console.print(Panel(table, title="COMMAND ROUTER", border_style="magenta"))


def show_action_intent(action_intent: object) -> None:
    console.print(
        Panel(
            Pretty(_model_to_dict(action_intent), expand_all=True),
            title="ACTION INTENT",
            border_style="yellow",
        )
    )


def show_intent_resolver(resolved: ResolvedIntent) -> None:
    table = Table(box=box.SIMPLE)
    table.add_column("Поле", style="cyan", no_wrap=True)
    table.add_column("Значення")
    table.add_row("source", resolved.source)
    table.add_row("matched", resolved.matched or "")
    table.add_row("action", resolved.action or "")
    table.add_row("target", resolved.target or "")
    if resolved.params:
        table.add_row("params", str(resolved.params))
    if resolved.original_text:
        table.add_row("original_text", resolved.original_text)
    if resolved.corrected_text:
        table.add_row("corrected_text", resolved.corrected_text)
    if resolved.correction_reason:
        table.add_row("correction_reason", resolved.correction_reason)
    if resolved.applied_corrections:
        table.add_row("applied_corrections", "; ".join(resolved.applied_corrections))
    table.add_row("confidence", f"{resolved.confidence:.2f}")
    table.add_row("risk", resolved.risk)
    table.add_row("need_confirmation", str(resolved.need_confirmation))
    table.add_row("pass to router", str(should_pass_to_router(resolved)))
    table.add_row("reason", resolved.reason)
    warning = resolver_debug_warning(resolved)
    if warning:
        table.add_row("warning", warning)
    console.print(Panel(table, title="INTENT RESOLVER", border_style="bright_blue"))


def show_resolver_clarification(resolved: ResolvedIntent) -> None:
    if resolved.risk != "safe" or resolved.need_confirmation:
        console.print(Panel("Дію не виконано: ризик або підтвердження не підтримуються у v0.1.", title="INTENT RESOLVER", border_style="red"))
        return
    if resolved.confidence < 0.65:
        console.print(Panel("Не до кінця зрозумів дію. Скажи коротко: гучніше, тихіше, пауза, відкрий Spotify тощо.", title="INTENT RESOLVER", border_style="yellow"))


def record_command_history(
    command_history: list[dict[str, object]],
    command_counter: int,
    user_text: str,
    result: RouterCommandResult,
) -> int:
    action = result.normalized_action or result.action
    if action not in ALLOWED_ACTIONS:
        return command_counter
    if result.is_safety_block or result.status == "blocked_confirmation_required":
        return command_counter

    command_counter += 1
    command_history.append(
        {
            "counter": command_counter,
            "user_text": user_text,
            "normalized_action": action,
            "normalized_target": result.normalized_target,
            "params": result.params or {},
            "executed": result.executed,
        }
    )
    del command_history[:-MAX_COMMAND_HISTORY]
    return command_counter


def should_try_resolver_for_result(result: RouterCommandResult, user_text: str) -> bool:
    if should_try_intent_resolver(result):
        return True

    if result.status == "blocked_dangerous" and not has_dangerous_text(user_text) and looks_like_command(user_text):
        return True

    return False


def _model_to_dict(model: object) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # Pydantic v2
    if hasattr(model, "dict"):
        return model.dict()  # Pydantic v1
    return {}


if __name__ == "__main__":
    sys.exit(cli())
