from pathlib import Path

from .fonts import bootstrap

# before anything imports Pango
bootstrap(Path(__file__).resolve().parent.parent / "assets" / "fonts")

from .app import main  # noqa: E402

main()
