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

model_app = typer.Typer(help="Manage runtime Ollama LLM model selection")
app.add_typer(model_app, name="model")

@model_app.command("status")
def model_status_cmd():
    """Display currently active Ollama model and list installed models."""
    setup_logging()
    from torrent_sentinel.clients.ollama import OllamaClient

    async def _status():
        storage = Storage()
        await storage.initialize()
        client = OllamaClient(storage=storage)
        active_model = await client.get_active_model()
        available = await client.get_available_models()

        console.print(Panel(
            f"[bold]Active Runtime Model:[/bold] [bold cyan]{active_model}[/bold cyan]\n"
            f"[bold]Configured Fallback:[/bold] {settings.OLLAMA_MODEL}\n"
            f"[bold]Ollama Endpoint:[/bold] {settings.OLLAMA_BASE_URL}",
            title="🤖 Sentinel AI Model Status",
            expand=False
        ))

        if available:
            table = Table(title="Installed Models on Ollama Host")
            table.add_column("Model Name", style="bold white")
            table.add_column("Status", style="green")

            for m in available:
                is_active = (m == active_model or m.split(":")[0] == active_model)
                status_str = "[bold green]ACTIVE[/bold green]" if is_active else "[dim]Available[/dim]"
                table.add_row(m, status_str)
            console.print(table)
        else:
            console.print("[yellow]Could not retrieve installed models from Ollama (service unreachable or empty).[/yellow]")

    asyncio.run(_status())

@model_app.command("set")
def model_set_cmd(
    model_name: str = typer.Argument(..., help="Model name (e.g. gemma2:27b, llama3.1:8b, mistral:latest)")
):
    """Set the active LLM model used for all AI operations."""
    setup_logging()
    from torrent_sentinel.clients.ollama import OllamaClient

    async def _set():
        storage = Storage()
        await storage.initialize()
        client = OllamaClient(storage=storage)
        cleaned = model_name.strip()
        await client.set_active_model(cleaned)
        console.print(f"[bold green]✅ Active runtime model successfully set to:[/bold green] [bold cyan]{cleaned}[/bold cyan]")
        console.print(f"[dim]Saved to persistent storage. All future judgements and autopilot cycles will use this model.[/dim]")

    asyncio.run(_set())

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

vpn_app = typer.Typer(help="Manage VPN Gateway state (enable, disable, status)")
app.add_typer(vpn_app, name="vpn")

@vpn_app.command("status")
def vpn_status_cmd():
    """Display current VPN connection status, location, and Off-VPN mode."""
    setup_logging()

    async def _status():
        storage = Storage()
        await storage.initialize()
        adapter = get_vpn_adapter()
        vpn_enabled = await storage.get_vpn_enabled()
        connected = await adapter.is_connected()
        profile = await adapter.get_current_profile()
        off_vpn_state = await storage.get_off_vpn_state()

        status_style = "bold green" if (vpn_enabled and connected) else "bold red"
        status_text = "CONNECTED" if (vpn_enabled and connected) else ("DISABLED" if not vpn_enabled else "DISCONNECTED")

        loc_text = profile.name if profile else ("Direct (No VPN)" if not vpn_enabled else "None")
        off_text = f"[bold yellow]ACTIVE[/bold yellow] (Torrent: {off_vpn_state.torrent_name or off_vpn_state.torrent_id}, Paused: {len(off_vpn_state.paused_torrent_ids)})" if (off_vpn_state and off_vpn_state.is_active) else "[dim]Inactive[/dim]"

        console.print(Panel(
            f"[bold]VPN Gateway Status:[/bold] [{status_style}]{status_text}[/{status_style}]\n"
            f"[bold]VPN Enabled in Config:[/bold] {'[green]YES[/green]' if vpn_enabled else '[red]NO[/red]'}\n"
            f"[bold]Active Location:[/bold] [cyan]{loc_text}[/cyan]\n"
            f"[bold]WireGuard Interface:[/bold] {settings.VPN_INTERFACE}\n"
            f"[bold]Solo Off-VPN Mode:[/bold] {off_text}",
            title="🛡️ VPN Gateway Status",
            expand=False
        ))

    asyncio.run(_status())

@vpn_app.command("disable")
def vpn_disable_cmd():
    """Disable VPN gateway and route traffic directly without VPN."""
    setup_logging()

    async def _disable():
        storage = Storage()
        await storage.initialize()
        adapter = get_vpn_adapter()
        await storage.set_vpn_enabled(False)
        await adapter.disconnect()
        console.print("[bold yellow]⚠️ VPN Gateway disabled.[/bold yellow]")
        console.print("[dim]WireGuard tunnel disconnected. Swarm traffic is now routing directly over host network.[/dim]")

    asyncio.run(_disable())

@vpn_app.command("enable")
def vpn_enable_cmd(
    location: Optional[str] = typer.Argument(None, help="Specific VPN location profile ID to connect to")
):
    """Enable VPN gateway and establish WireGuard tunnel."""
    setup_logging()

    async def _enable():
        storage = Storage()
        await storage.initialize()
        adapter = get_vpn_adapter()
        await storage.set_vpn_enabled(True)
        available = await adapter.get_available_locations()
        target = None
        if location and available:
            pref = location.lower().strip()
            target = next((p for p in available if p.id.lower() == pref or p.name.lower() == pref), None)
        if not target and available:
            top_locations = await storage.get_top_locations(limit=1)
            if top_locations:
                target = next((p for p in available if p.id == top_locations[0]), None)
            if not target:
                target = available[0]

        if target:
            success = await adapter.rotate_to(target)
            if success:
                console.print(f"[bold green]✓ VPN Gateway enabled and connected to:[/bold green] [bold cyan]{target.name}[/bold cyan]")
            else:
                console.print(f"[bold red]✗ Failed to establish VPN connection to:[/bold red] {target.name}")
        else:
            console.print("[bold yellow]VPN enabled in settings, but no WireGuard profiles were found in configs directory.[/bold yellow]")

    asyncio.run(_enable())

off_vpn_app = typer.Typer(help="Manage Solo Off-VPN execution (pause fleet, disconnect VPN, run single torrent)")
app.add_typer(off_vpn_app, name="off-vpn")

@off_vpn_app.command("start")
def off_vpn_start_cmd(
    identifier: str = typer.Argument(..., help="Torrent ID or Hash to run off-VPN"),
    no_auto_restore: bool = typer.Option(False, "--no-auto-restore", help="Do not automatically restore VPN when download finishes")
):
    """Run a specific torrent off-VPN (pauses all other torrents and disables VPN)."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient
    from torrent_sentinel.models import OffVpnState
    from datetime import datetime, timezone

    async def _start():
        storage = Storage()
        await storage.initialize()
        adapter = get_vpn_adapter()
        transmission = TransmissionClient()

        torrents = await transmission.get_torrents()
        target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
        if not target:
            console.print(f"[bold red]Torrent '{identifier}' not found in Transmission.[/bold red]")
            return

        cur_prof = await adapter.get_current_profile()
        prev_loc = cur_prof.id if cur_prof else None

        running_other_ids = [t.id for t in torrents if t.id != target.id and str(t.status) not in ("0", "stopped", "paused")]
        all_other_ids = [t.id for t in torrents if t.id != target.id]

        if all_other_ids:
            console.print(f"[cyan]Pausing {len(all_other_ids)} other torrent(s) in Transmission...[/cyan]")
            await transmission.pause_torrents(all_other_ids)

        console.print(f"[cyan]Starting target torrent '{target.name}'...[/cyan]")
        await transmission.resume(target.id)

        console.print("[yellow]Disconnecting WireGuard VPN tunnel...[/yellow]")
        await adapter.disconnect()

        state = OffVpnState(
            is_active=True,
            torrent_id=target.id,
            torrent_hash=target.hash,
            torrent_name=target.name,
            paused_torrent_ids=running_other_ids,
            previous_vpn_location=prev_loc,
            started_at=datetime.now(timezone.utc),
            auto_restore_on_complete=(not no_auto_restore)
        )
        await storage.set_off_vpn_state(state)

        console.print(Panel(
            f"[bold green]🚀 Solo Off-VPN Mode Active[/bold green]\n\n"
            f"[bold]Target Torrent:[/bold] {target.name} (ID: {target.id})\n"
            f"[bold]Paused Queue Torrents:[/bold] {len(running_other_ids)}\n"
            f"[bold]VPN Gateway:[/bold] [bold red]DISCONNECTED (Direct Routing)[/bold red]\n"
            f"[bold]Auto-Restore on 100% Completion:[/bold] {'[green]YES[/green]' if not no_auto_restore else '[yellow]NO[/yellow]'}\n\n"
            f"[dim]Run 'torrent-sentinel off-vpn stop' at any time to re-enable VPN and resume the fleet.[/dim]",
            title="⚠️ Solo Off-VPN Started",
            expand=False
        ))

    asyncio.run(_start())

@off_vpn_app.command("stop")
def off_vpn_stop_cmd():
    """Stop Solo Off-VPN mode: re-establish VPN gateway and resume paused fleet."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient

    async def _stop():
        storage = Storage()
        await storage.initialize()
        state = await storage.get_off_vpn_state()
        if not state or not state.is_active:
            console.print("[yellow]Solo Off-VPN mode is not currently active.[/yellow]")
            return

        adapter = get_vpn_adapter()
        transmission = TransmissionClient()

        console.print("[cyan]Re-establishing WireGuard VPN tunnel...[/cyan]")
        await storage.set_vpn_enabled(True)
        available = await adapter.get_available_locations()
        target_profile = None
        if state.previous_vpn_location and available:
            target_profile = next((p for p in available if p.id == state.previous_vpn_location), None)
        if not target_profile and available:
            top_locations = await storage.get_top_locations(limit=1)
            if top_locations:
                target_profile = next((p for p in available if p.id == top_locations[0]), None)
            if not target_profile:
                target_profile = available[0]

        if target_profile:
            await adapter.rotate_to(target_profile)
            console.print(f"[bold green]✓ VPN re-connected to:[/bold green] {target_profile.name}")

        if state.paused_torrent_ids:
            console.print(f"[cyan]Resuming {len(state.paused_torrent_ids)} previously paused torrent(s)...[/cyan]")
            await transmission.resume_torrents(state.paused_torrent_ids)

        await storage.set_off_vpn_state(None)
        console.print("[bold green]✅ Solo Off-VPN mode ended. Normal fleet operations restored.[/bold green]")

    asyncio.run(_stop())

@off_vpn_app.command("status")
def off_vpn_status_cmd():
    """Check current Solo Off-VPN mode status."""
    setup_logging()

    async def _status():
        storage = Storage()
        await storage.initialize()
        state = await storage.get_off_vpn_state()
        if not state or not state.is_active:
            console.print("[green]Solo Off-VPN mode is inactive. All torrents operate under standard VPN gateway.[/green]")
            return

        console.print(Panel(
            f"[bold yellow]⚠️ SOLO OFF-VPN MODE IS ACTIVE[/bold yellow]\n\n"
            f"[bold]Exclusive Torrent:[/bold] {state.torrent_name or state.torrent_id}\n"
            f"[bold]Paused Queue Torrents:[/bold] {len(state.paused_torrent_ids)}\n"
            f"[bold]Started At:[/bold] {state.started_at.strftime('%Y-%m-%d %H:%M:%S UTC') if state.started_at else 'Unknown'}\n"
            f"[bold]Previous VPN Location:[/bold] {state.previous_vpn_location or 'Default'}\n"
            f"[bold]Auto-Restore upon completion:[/bold] {'YES' if state.auto_restore_on_complete else 'NO'}",
            title="Solo Off-VPN Status",
            expand=False
        ))

    asyncio.run(_status())

@vpn_app.command("port-check")
def vpn_port_check_cmd():
    """Test if Transmission's peer-listening port is open/forwarded on the current connection."""
    test_port_cmd()

@app.command("test-port")
def test_port_cmd():
    """Test Transmission's incoming peer listening port (verifies if VPN/NAT is firewalled)."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient

    async def _test():
        client = TransmissionClient()
        adapter = get_vpn_adapter()
        connected = await adapter.is_connected()
        prof = await adapter.get_current_profile()
        loc = prof.name if prof else "Direct / None"

        console.print("[cyan]Testing Transmission peer listening port externally...[/cyan]")
        res = await client.port_test()
        port = res.get("port")
        is_open = res.get("port_open", False)

        port_color = "green" if is_open else "red"
        status_text = "OPEN (Accepting Inbound Connections)" if is_open else "CLOSED / FIREWALLED"
        vpn_status = f"[green]Active ({loc})[/green]" if connected else "[yellow]Disconnected / Direct[/yellow]"

        table = Table(title="Transmission Network Port Test")
        table.add_column("Parameter", style="cyan", no_wrap=True)
        table.add_column("Value", style="bold")
        table.add_row("Peer Listening Port", str(port or "Unknown"))
        table.add_row("Port Status", f"[{port_color}]{status_text}[/{port_color}]")
        table.add_row("VPN Connection", vpn_status)

        console.print(table)

        if not is_open:
            console.print(Panel(
                "[bold yellow]⚠️ Inbound Port is Closed / Firewalled[/bold yellow]\n\n"
                "• Your client cannot receive incoming connections from other firewalled peers.\n"
                "• If a torrent swarm consists of peers behind NAT/firewalls, you [bold red]cannot connect to them[/bold red].\n"
                "• [bold green]Recommended fix:[/bold green] Use a VPN provider with WireGuard Port Forwarding support (AirVPN, ProtonVPN, TorGuard) or a self-hosted VPS.",
                title="Troubleshooting Port Forwarding",
                expand=False
            ))
        else:
            console.print("[bold green]✓ Port is open! Inbound peer connections will connect properly.[/bold green]")

    asyncio.run(_test())

@app.command("trackers")
def trackers_cmd(
    identifier: str = typer.Argument(..., help="Torrent ID or Hash to inspect trackers for")
):
    """Inspect tracker responses, errors, and detect VPN IP blocks for a torrent."""
    setup_logging()
    from torrent_sentinel.clients.transmission import TransmissionClient
    from torrent_sentinel.engine.diagnostics import summarize_torrent_trackers

    async def _trackers():
        client = TransmissionClient()
        torrents = await client.get_torrents()
        target = next((t for t in torrents if t.id == identifier or t.hash.lower() == identifier.lower()), None)
        if not target:
            console.print(f"[bold red]Torrent '{identifier}' not found in Transmission.[/bold red]")
            return

        summary = summarize_torrent_trackers(getattr(target, "tracker_stats", []) or [])
        
        table = Table(title=f"Trackers for '{target.name}'")
        table.add_column("Host", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Seeds", justify="right")
        table.add_column("Peers", justify="right")
        table.add_column("Last Result", style="dim")

        for tr in summary["trackers"]:
            status = tr["status"]
            if status == "healthy":
                s_style = "[green]HEALTHY[/green]"
            elif status == "blocked":
                s_style = "[bold red]BLOCKED (VPN/WAF)[/bold red]"
            elif status == "unreachable":
                s_style = "[yellow]UNREACHABLE[/yellow]"
            else:
                s_style = f"[white]{status.upper()}[/white]"

            table.add_row(
                tr["host"],
                s_style,
                str(tr["seeders"]),
                str(tr["peers"]),
                tr["last_result"] or "None"
            )

        console.print(table)
        
        if summary["vpn_blocked_suspect"]:
            console.print(Panel(
                f"[bold red]⚠️ Potential VPN IP Block Detected![/bold red]\n\n"
                f"{summary['blocked_count']} tracker(s) returned HTTP 403, Cloudflare challenges, or access denied.\n"
                f"Run 'torrent-sentinel off-vpn start {target.id}' to test downloading off-VPN or inject public UDP trackers.",
                title="VPN Block Warning",
                expand=False
            ))

    asyncio.run(_trackers())

if __name__ == "__main__":
    app()



