import asyncio
import logging
import sys
from typing import Optional

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

if __name__ == "__main__":
    app()
