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

if __name__ == "__main__":
    app()

