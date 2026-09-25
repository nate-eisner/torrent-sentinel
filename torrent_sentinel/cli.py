import asyncio
import logging
import sys
from typing import Optional

import click
# Compatibility patch for Typer 0.9 / Click 8.2+ help formatting
_orig_make_metavar = click.core.Parameter.make_metavar
def _safe_make_metavar(self, ctx=None):
    if ctx is None:
        ctx = click.get_current_context(silent=True) or click.Context(click.Command('dummy'))
    return _orig_make_metavar(self, ctx)
click.core.Parameter.make_metavar = _safe_make_metavar

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel


from torrent_sentinel.config import settings
from torrent_sentinel.logging_config import setup_logging
from torrent_sentinel.engine.daemon import SentinelDaemon
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.vpn import get_vpn_adapter

app = typer.Typer(help="Torrent Sentinel CLI")
console = Console()

@app.command()
def run(
    host: Optional[str] = typer.Option(None, help="Host to bind Web UI"),
    port: Optional[int] = typer.Option(None, help="Port to bind Web UI"),
    headless: bool = typer.Option(False, "--headless", help="Run daemon only without Web UI")
):
    """Start Torrent Sentinel (Web UI & background daemon)."""
    setup_logging()
    logger = logging.getLogger("torrent_sentinel.cli")

    if headless:
        logger.info("Initializing Torrent Sentinel in HEADLESS mode (daemon only)...")
        adapter = get_vpn_adapter()
        daemon = SentinelDaemon(adapter)
        try:
            asyncio.run(daemon.run())
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user (SIGINT/KeyboardInterrupt).")
    else:
        bind_host = host or settings.WEB_HOST
        bind_port = port or settings.WEB_PORT
        logger.info("Starting Torrent Sentinel Web UI & Daemon on http://%s:%d ...", bind_host, bind_port)
        import uvicorn
        uvicorn_log_level = settings.LOG_LEVEL.lower()
        uvicorn.run(
            "torrent_sentinel.api.router:app",
            host=bind_host,
            port=bind_port,
            log_level=uvicorn_log_level
        )

@app.command()
def history():
    """Display recent rotation events."""
    setup_logging()
    storage = Storage()

    async def _get_history():
        await storage.initialize()
        return await storage.get_history()

    events = asyncio.run(_get_history())
    
    if not events:
        console.print("[yellow]No history found in database.[/yellow]")
        return

    table = Table(title="Rotation History")
    table.add_column("Timestamp", style="cyan")
    table.add_column("From", style="magenta")
    table.add_column("To", style="green")
    table.add_column("Reason", style="white")

    for event in events:
        table.add_row(
            event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            event.from_location,
            event.to_location,
            event.reason
        )

    console.print(table)

@app.command()
def test_ollama():
    """Test connection to local Ollama instance."""
    setup_logging()
    from torrent_sentinel.clients.ollama import OllamaClient
    client = OllamaClient()
    
    console.print(f"Testing Ollama at {settings.OLLAMA_BASE_URL} (configured model: {settings.OLLAMA_MODEL})...")
    async def _test():
        healthy = await client.health_check()
        if healthy:
            console.print("[bold green]✓ Ollama is reachable and healthy![/bold green]")
        else:
            console.print("[bold red]✗ Ollama is unreachable or unhealthy. Check SENTINEL_OLLAMA_BASE_URL.[/bold red]")

    asyncio.run(_test())

@app.command()
def judge(
    identifier: str = typer.Argument(..., help="Torrent ID or Hash to judge"),
    prompt: Optional[str] = typer.Option(None, "--prompt", "-p", help="Custom query or instructions for the LLM")
):
    """Ask LLM to perform deep diagnostic judgement on a torrent download."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient
    from torrent_sentinel.clients.ollama import OllamaClient
    from torrent_sentinel.engine.diagnostics import Diagnostics
    from torrent_sentinel.engine.storage import Storage

    async def _judge():
        storage = Storage()
        await storage.initialize()
        transmission = TransmissionClient()
        ollama = OllamaClient()
        diagnostics = Diagnostics(transmission, ollama)

        torrents = await transmission.get_torrents()
        target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
        if not target:
            console.print(f"[bold red]Torrent '{identifier}' not found in Transmission.[/bold red]")
            return

        console.print(f"[cyan]Evaluating torrent '{target.name}' with LLM ({settings.OLLAMA_MODEL})...[/cyan]")
        judgement = await diagnostics.judge_torrent(target, user_prompt=prompt)
        if not judgement:
            console.print("[bold red]Failed to get judgement from Ollama. Ensure Ollama is running.[/bold red]")
            return

        await storage.record_judgement(judgement)

        verdict_color = "green" if judgement.verdict.value == "healthy" else ("yellow" if "stalled" in judgement.verdict.value or "boost" in judgement.verdict.value else "red")
        
        info = (
            f"[bold]Verdict:[/bold] [{verdict_color}]{judgement.verdict.value.upper()}[/{verdict_color}]\n"
            f"[bold]Viability Score:[/bold] {judgement.viability_score * 100:.1f}%\n"
            f"[bold]Recommended Action:[/bold] [bold cyan]{judgement.recommended_action.value.upper()}[/bold cyan] - {judgement.action_explanation}\n"
            f"[bold]Confidence:[/bold] {judgement.confidence * 100:.0f}%\n\n"
            f"[bold]Reasoning:[/bold]\n{judgement.reasoning}\n"
        )
        if judgement.tracker_analysis:
            info += f"\n[bold]Tracker Analysis:[/bold]\n{judgement.tracker_analysis}\n"

        console.print(Panel(info, title=f"AI Judgement: {target.name}", expand=False))

    asyncio.run(_judge())

@app.command()
def assess():
    """Ask LLM to assess entire swarm and recommend VPN/torrent actions."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient
    from torrent_sentinel.clients.ollama import OllamaClient
    from torrent_sentinel.engine.diagnostics import Diagnostics
    from torrent_sentinel.vpn import get_vpn_adapter

    async def _assess():
        transmission = TransmissionClient()
        ollama = OllamaClient()
        diagnostics = Diagnostics(transmission, ollama)
        vpn_adapter = get_vpn_adapter()

        torrents = await transmission.get_torrents()
        if not torrents:
            console.print("[yellow]No active torrents found in Transmission.[/yellow]")
            return

        prof = await vpn_adapter.get_current_profile()
        vpn_info = {"current_location": prof.name, "endpoint": prof.endpoint} if prof else {}

        console.print(f"[cyan]Assessing {len(torrents)} torrent(s) and VPN state with LLM ({settings.OLLAMA_MODEL})...[/cyan]")
        assessment = await diagnostics.assess_swarm(torrents, vpn_info=vpn_info)
        if not assessment:
            console.print("[bold red]Swarm assessment failed. Check Ollama service.[/bold red]")
            return

        console.print(Panel(
            f"[bold]Overall Summary:[/bold] {assessment.overall_summary}\n"
            f"[bold]VPN Status:[/bold] {assessment.vpn_health_verdict.upper()}\n"
            f"[bold]Rotate VPN Recommended:[/bold] {'[bold red]YES[/bold red]' if assessment.should_rotate_vpn else '[bold green]NO[/bold green]'}\n"
            f"[bold]VPN Reasoning:[/bold] {assessment.vpn_reasoning}",
            title="Swarm Assessment",
            expand=False
        ))

        table = Table(title="Individual Torrent Judgements")
        table.add_column("Torrent", style="white")
        table.add_column("Verdict", style="cyan")
        table.add_column("Viability", style="magenta")
        table.add_column("Action", style="green")

        for j in assessment.torrent_judgements:
            table.add_row(
                j.torrent_name[:35],
                j.verdict.value,
                f"{j.viability_score * 100:.0f}%",
                j.recommended_action.value
            )
        console.print(table)

    asyncio.run(_assess())

@app.command()
def ask(
    question: str = typer.Argument(..., help="Question or prompt about active downloads")
):
    """Ask Sentinel LLM assistant any question about your active downloads."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient
    from torrent_sentinel.clients.ollama import OllamaClient

    async def _ask():
        transmission = TransmissionClient()
        ollama = OllamaClient()
        torrents = await transmission.get_torrents()
        
        context = {
            "total_torrents": len(torrents),
            "torrents": [
                {
                    "name": t.name,
                    "progress": round(t.progress * 100, 1),
                    "rate_download_kbps": round(t.rate_download / 1024, 1),
                    "peers": t.peers_connected,
                    "seeds": t.peers_sending_to_us,
                    "error": t.error_string or t.error
                } for t in torrents
            ]
        }

        console.print(f"[cyan]Querying Sentinel AI about downloads...[/cyan]\n")
        answer = await ollama.chat_about_downloads(question, context)
        console.print(Panel(answer, title=f"AI Response: '{question}'", expand=False))

    asyncio.run(_ask())

autopilot_app = typer.Typer(help="AI Autopilot Engine controls and flight log")
app.add_typer(autopilot_app, name="autopilot")

@autopilot_app.command("status")
def autopilot_status_cmd():
    """Display current Autopilot mode, last run time, and recent flight events."""
    setup_logging()
    from torrent_sentinel.models import AutopilotMode

    async def _status():
        storage = Storage()
        await storage.initialize()
        mode = await storage.get_autopilot_mode()
        events = await storage.get_autopilot_events(limit=15)

        mode_style = "bold green" if mode == "full" else ("bold blue" if mode == "advisory" else "yellow")
        console.print(Panel(
            f"[bold]Autopilot Mode:[/bold] [{mode_style}]{mode.upper()}[/{mode_style}]\n"
            f"[bold]Safety Guardrails:[/bold] Cooldown: {settings.AUTOPILOT_ROTATION_COOLDOWN_MINUTES}m | Min Failover Conf: {settings.AUTOPILOT_MIN_CONFIDENCE_FAILOVER*100:.0f}% | Max Failover/Cycle: {settings.AUTOPILOT_MAX_FAILOVERS_PER_CYCLE}",
            title="✈️ AI Autopilot Status",
            expand=False
        ))

        if not events:
            console.print("[dim]No flight events recorded in database yet.[/dim]")
            return

        table = Table(title="Recent Autonomous Flight Events")
        table.add_column("Timestamp", style="cyan")
        table.add_column("Mode", style="white")
        table.add_column("Action", style="magenta")
        table.add_column("Target", style="bold white")
        table.add_column("Conf", style="green")
        table.add_column("Status", style="yellow")
        table.add_column("Reasoning", style="dim white")

        for ev in events:
            status_str = "[bold green]Executed[/bold green]" if ev.executed else "[dim]Simulated[/dim]"
            table.add_row(
                ev.timestamp.strftime("%H:%M:%S"),
                ev.mode.upper(),
                ev.action_type,
                (ev.target_name or ev.target_id or "")[:25],
                f"{ev.confidence*100:.0f}%",
                status_str,
                ev.reasoning[:40] + ("..." if len(ev.reasoning) > 40 else "")
            )
        console.print(table)

    asyncio.run(_status())

@autopilot_app.command("mode")
def autopilot_mode_cmd(
    mode: str = typer.Argument(..., help="Mode to set: 'off', 'advisory', or 'full'")
):
    """Set the AI Autopilot operational mode (off, advisory, full)."""
    setup_logging()
    from torrent_sentinel.models import AutopilotMode
    clean_mode = mode.lower().strip()
    if clean_mode not in ("off", "advisory", "full"):
        console.print(f"[bold red]Invalid mode '{mode}'. Choose 'off', 'advisory', or 'full'.[/bold red]")
        sys.exit(1)

    async def _set_mode():
        storage = Storage()
        await storage.initialize()
        await storage.set_autopilot_mode(clean_mode)
        console.print(f"[bold green]✓ Autopilot mode updated to '{clean_mode.upper()}'.[/bold green]")

    asyncio.run(_set_mode())

@autopilot_app.command("run")
def autopilot_run_cmd(
    mode: Optional[str] = typer.Option(None, help="Override mode for this single flight run ('advisory' or 'full')"),
    force: bool = typer.Option(False, "--force", help="Force run regardless of interval")
):
    """Trigger an autonomous flight planning and execution cycle immediately."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient
    from torrent_sentinel.clients.ollama import OllamaClient
    from torrent_sentinel.services.tracker_service import TrackerService
    from torrent_sentinel.engine.booster import TorrentBooster
    from torrent_sentinel.engine.diagnostics import Diagnostics
    from torrent_sentinel.engine.decision import DecisionEngine
    from torrent_sentinel.engine.autopilot import AutopilotEngine
    from torrent_sentinel.models import AutopilotMode

    async def _run():
        storage = Storage()
        await storage.initialize()
        adapter = get_vpn_adapter()
        transmission = TransmissionClient()
        ollama = OllamaClient()
        tracker_service = TrackerService(settings.TRACKER_LIST_URLS)
        diagnostics = Diagnostics(transmission, ollama)
        decision_engine = DecisionEngine(diagnostics, storage, adapter)
        booster = TorrentBooster(transmission, tracker_service, storage, decision_engine.notifications, diagnostics)
        autopilot = AutopilotEngine(transmission, booster, decision_engine, adapter, storage, ollama, decision_engine.notifications)

        mode_override = None
        if mode:
            try:
                mode_override = AutopilotMode(mode.lower().strip())
            except ValueError:
                console.print(f"[bold red]Invalid mode override '{mode}'.[/bold red]")
                sys.exit(1)

        torrents = await transmission.get_torrents()
        console.print(f"[cyan]✈️ Executing AI Autopilot cycle for {len(torrents)} torrent(s)...[/cyan]")

        plan = await autopilot.run_autopilot_cycle(
            torrents=torrents,
            force=force,
            mode_override=mode_override
        )

        if not plan:
            console.print("[yellow]Autopilot cycle did not produce a plan (off or skipped).[/yellow]")
            return

        console.print(Panel(
            f"[bold]Summary:[/bold] {plan.summary}\n"
            f"[bold]Mode:[/bold] {plan.mode.value.upper()}\n"
            f"[bold]VPN Verdict:[/bold] {plan.vpn_health_verdict.upper()}\n"
            f"[bold]Rotate VPN:[/bold] {'[bold red]YES[/bold red]' if plan.should_rotate_vpn else '[bold green]NO[/bold green]'}\n"
            f"[bold]VPN Reasoning:[/bold] {plan.vpn_reasoning}"
            + (f"\n[bold yellow]Guardrails Applied:[/bold yellow] {', '.join(plan.guardrails_applied)}" if plan.guardrails_applied else ""),
            title="✈️ Autopilot Flight Plan",
            expand=False
        ))

        if plan.actions:
            table = Table(title="Flight Actions")
            table.add_column("Type", style="magenta")
            table.add_column("Target", style="bold white")
            table.add_column("Conf", style="green")
            table.add_column("Viability", style="cyan")
            table.add_column("Executed", style="yellow")
            table.add_column("Result / Reason", style="dim white")

            for a in plan.actions:
                status_str = "[bold green]YES[/bold green]" if a.executed else "[dim]NO (Simulated)[/dim]"
                table.add_row(
                    a.action_type.value,
                    (a.target_name or a.target_id or "")[:30],
                    f"{a.confidence*100:.0f}%",
                    f"{a.viability_score*100:.0f}%",
                    status_str,
                    (a.execution_result or a.reasoning)[:50]
                )
            console.print(table)
        else:
            console.print("[green]No actions required; fleet is operating optimally.[/green]")

    asyncio.run(_run())

if __name__ == "__main__":
    app()

