"""Teach fontconfig about the bundled pixel font, for this process only.

Nothing is installed into the user's font directories. fontconfig reads its
whole configuration once, at first use, from $FONTCONFIG_FILE — so pointing
that at a generated file which includes the system config *and* our asset
directory gets the font seen without touching anything global.

Import order matters: this must run before Pango (and therefore before Gtk)
initialises, which is why __main__ calls it first.
"""
from __future__ import annotations

import os
from pathlib import Path

CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>{fonts}</dir>
  <cachedir>{cache}</cachedir>
</fontconfig>
"""


def bootstrap(font_dir: Path) -> bool:
    """Point fontconfig at font_dir. False if the caller should just use Sans."""
    if "FONTCONFIG_FILE" in os.environ or not font_dir.is_dir():
        return False
    try:
        cache = Path(
            os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        ) / "mewmori" / "fontconfig"
        cache.mkdir(parents=True, exist_ok=True)
        conf = cache.parent / "fonts.conf"
        conf.write_text(CONF.format(fonts=font_dir.resolve(), cache=cache))
        os.environ["FONTCONFIG_FILE"] = str(conf)
        return True
    except OSError:
        return False
