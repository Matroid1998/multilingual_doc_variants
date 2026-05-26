"""Top-level CLI: `uv run mdv stage1 | stage2 | stage3 | stage4 | all`."""
from __future__ import annotations

import typer

from .config import BENCH1_TARGET_COUNT, BENCH2_TARGET_COUNT, RNG_SEED

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def stage1(
    refresh_download: bool = False,
    refresh_wikidata: bool = False,
    with_wikidata: bool = False,
):
    """Build the ChEBI KG slice. Pass --with-wikidata to also fetch Wikipedia titles via SPARQL."""
    from .stage1_kg.build import build
    build(
        refresh_download=refresh_download,
        refresh_wikidata=refresh_wikidata,
        with_wikidata=with_wikidata,
    )


@app.command()
def stage2():
    """Link chemistry mentions across all corpus documents."""
    from .stage2_link.build import build
    build()


@app.command()
def stage3(target: int = BENCH1_TARGET_COUNT, seed: int = RNG_SEED):
    """Compile Benchmark 1 (alias-graph) instances."""
    from .stage3_idea1.build import build
    build(target=target, seed=seed)


@app.command()
def stage4(
    target: int = BENCH2_TARGET_COUNT,
    seed: int = RNG_SEED,
    limit: int | None = None,
):
    """Generate Benchmark 2 (code-switched) variants via LLM."""
    from .stage4_idea2.build import build
    build(target=target, seed=seed, limit=limit)


@app.command()
def all(
    refresh_download: bool = False,
    refresh_wikidata: bool = False,
    with_wikidata: bool = False,
    target1: int = BENCH1_TARGET_COUNT,
    target2: int = BENCH2_TARGET_COUNT,
    seed: int = RNG_SEED,
    limit: int | None = None,
):
    """Run all four stages sequentially."""
    from .stage1_kg.build import build as build1
    from .stage2_link.build import build as build2
    from .stage3_idea1.build import build as build3
    from .stage4_idea2.build import build as build4

    build1(
        refresh_download=refresh_download,
        refresh_wikidata=refresh_wikidata,
        with_wikidata=with_wikidata,
    )
    build2()
    build3(target=target1, seed=seed)
    build4(target=target2, seed=seed, limit=limit)


if __name__ == "__main__":
    app()
