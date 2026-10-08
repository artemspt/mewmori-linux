import faulthandler
import signal
from pathlib import Path

from .fonts import bootstrap

# The cat wedged once with a live D-Bus name, a dead frame loop and an empty
# log: whisper and ollama had been fighting over a card that fits neither
# comfortably, and the damage was native, so nothing reached Python to print.
# faulthandler turns both halves of that into something readable — a stack for
# every thread on a hard crash, and the same on demand:
#
#     kill -USR1 $(pgrep -f 'm mewmori')
#
# It costs nothing until something goes wrong, and `kill -9` remains the only
# way out of a main thread stuck inside C.
faulthandler.enable()
faulthandler.register(signal.SIGUSR1, all_threads=True, chain=True)

# before anything imports Pango
bootstrap(Path(__file__).resolve().parent.parent / "assets" / "fonts")

from .app import main  # noqa: E402

main()
