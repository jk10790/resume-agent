"""
Progress tracking utilities using Rich library.
"""

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.console import Console
from contextlib import contextmanager
from typing import Optional

console = Console()


@contextmanager
def track_operation(description: str, total: Optional[int] = None):
    """
    Context manager for tracking operation progress.
    
    Args:
        description: Description of the operation
        total: Total number of steps (None for indeterminate)
    
    Usage:
        with track_operation("Processing files", total=10) as progress:
            for i in range(10):
                # Do work
                progress.update(task, advance=1)
    """
    columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
    ]
    
    if total:
        columns.extend([
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ])
    else:
        columns.append(TimeElapsedColumn())
    
    with Progress(*columns, console=console) as progress:
        task = progress.add_task(description, total=total)
        yield progress


def print_success(message: str):
    """Print success message"""
    console.print(f"[green]✅ {message}[/green]")


def print_error(message: str):
    """Print error message"""
    console.print(f"[red]❌ {message}[/red]")


def print_warning(message: str):
    """Print warning message"""
    console.print(f"[yellow]⚠️ {message}[/yellow]")


def print_info(message: str):
    """Print info message"""
    console.print(f"[blue]ℹ️ {message}[/blue]")


def print_table(title: str, columns: list, rows: list):
    """Print a formatted table"""
    from rich.table import Table
    
    table = Table(title=title, show_header=True, header_style="bold magenta")
    
    for col in columns:
        table.add_column(col)
    
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    
    console.print(table)
