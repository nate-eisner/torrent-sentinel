import asyncio
import logging
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from torrent_sentinel.config import settings
from torrent_sentinel.engine.daemon import SentinelDaemon
from torrent_sentinel.engine.storage import Storage
from torrent_sentinel.vpn.mock import MockVPNAdapter

app = typer.Typer(help="Torrent Sentinel CLI")
console = Console()

@app.command()
def run():
    """Start the Torrent Sentinel daemon."""
    console.print("[bold green]Starting Torrent Sentinel Daemon...[/bold green]")
    # Using Mock for default run to ensure it works without Unraid environment
    adapter = MockVPNAdapter() 
    daemon = SentinelDaemon(adapter)
    
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Daemon stopped by user.[/bold yellow]")

@app.command()
def history():
    """Display recent rotation events."""
    storage = Storage()
    # Note: This is synchronous in the current implementation, 
    # but for a CLI it's acceptable or can be wrapped in asyncio.run
    async def _get_history():
        await storage.initialize()
        return await storage.get_history()

    events = asyncio.run(_get_history())
    
    if not events:
        console.print("[yellow]No history found.[/yellow]")
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
    from torrent_sentinel.clients.ollama import OllamaClient
    client = OllamaClient()
    
    console.print(f"Testing Ollama at {settings.OLLAMA_BASE_URL}...")
    async def _test():
        healthy = await client.health_check()
        if healthy:
            console.print("[bold green]Ollama is reachable and healthy![/bold green]")
        else:
            console.print("[bold red]Ollama is unreachable or unhealthy.[/bold red]")

    asyncio.run(_test())

if __name__ == "__main__":
    app()
