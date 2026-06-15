from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.prompt import Prompt
from rich.table import Table

from command_router import CommandResult as RouterCommandResult
from command_router import CommandRouter
from command_router import should_try_intent_resolver
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


MAX_HISTORY_MESSAGES = 40
MAX_COMMAND_HISTORY = 10


console = Console()


def main() -> None:
    client = OllamaClient()
    active_history: list[dict[str, str]] = []
    session_summary = ""
    debug = False
    router = CommandRouter(dry_run=True)
    resolver = IntentResolver(client)
    command_history: list[dict[str, object]] = []
    command_counter = 0

    show_startup(client)

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
        )
        if command_result.exit_requested:
            break
        if command_result.handled:
            session_summary = command_result.session_summary
            debug = command_result.debug
            continue

        active_history.append({"role": "user", "content": user_text})
        context_messages = build_context_messages(active_history, session_summary)

        with console.status("[bold green]Арвіс думає...[/bold green]", spinner="dots"):
            raw_response, error = client.chat(context_messages)

        if error:
            console.print(Panel(error, title="OLLAMA ERROR", border_style="red"))
            active_history.pop()
            continue

        parsed, warnings = parse_assistant_response(raw_response or "", debug=debug)
        router_results: list[RouterCommandResult] = []
        resolver_results: list[ResolvedIntent] = []
        resolver_clarification: ResolvedIntent | None = None
        final_router_result: RouterCommandResult | None = None
        final_resolver_result: ResolvedIntent | None = None

        if parsed.action_intent is not None:
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
            resolved = resolver.resolve(user_text, command_history)
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

        if parsed.action_intent is not None:
            console.print(
                Panel(
                    Pretty(_model_to_dict(parsed.action_intent), expand_all=True),
                    title="ACTION INTENT",
                    border_style="yellow",
                )
            )

        for resolved in resolver_results:
            show_intent_resolver(resolved)

        for router_result in router_results:
            show_command_router(router, router_result)

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


class ReplCommandResult:
    def __init__(
        self,
        handled: bool,
        exit_requested: bool,
        session_summary: str,
        debug: bool,
    ) -> None:
        self.handled = handled
        self.exit_requested = exit_requested
        self.session_summary = session_summary
        self.debug = debug


def handle_command(
    user_text: str,
    active_history: list[dict[str, str]],
    session_summary: str,
    debug: bool,
    router: CommandRouter,
) -> ReplCommandResult:
    command = user_text.lower()

    if command in {"/exit", "/quit"}:
        console.print("[dim]Вихід.[/dim]")
        return ReplCommandResult(True, True, session_summary, debug)

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
    main()
