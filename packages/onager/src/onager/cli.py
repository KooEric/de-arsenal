"""Onager CLI."""

from pathlib import Path

import typer

from arsenal_core.errors import ArsenalError
from onager.compact import compact_dataset

app = typer.Typer(help="Onager — safe Parquet small-file compaction.")


@app.command()
def compact(
    dataset: Path,
    target_bytes: int = typer.Option(128 * 1024 * 1024, "--target-bytes"),
    min_files: int = typer.Option(2, "--min-files"),
    max_input_bytes: int | None = typer.Option(None, "--max-input-bytes"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Compact selected local Parquet files; inspect the plan with --dry-run."""
    try:
        report = compact_dataset(
            dataset,
            target_bytes=target_bytes,
            min_files=min_files,
            max_input_bytes=max_input_bytes,
            dry_run=dry_run,
        )
    except ArsenalError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(
        f"selected={report.selected_files} preserved={report.preserved_files} "
        f"input_bytes={report.input_bytes} output_bytes={report.output_bytes} "
        f"compacted={report.compacted}"
    )


if __name__ == "__main__":
    app()
