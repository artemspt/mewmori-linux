#!/bin/bash
# Launcher used both by hand and by the autostart entry.
#
# The cat itself needs nothing but the system PyGObject. The voice half needs
# real packages (see requirements.txt), and Debian refuses `pip install --user`
# on a system Python, so it wants a virtualenv.
#
# Candidates are *tested*, not guessed: an interpreter is only used if it can
# actually import gi. A virtualenv made without --system-site-packages cannot —
# and that is the default for one PyCharm or uv creates in the project folder —
# so picking it by name alone would leave the cat with no window at all.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

# command -v, not [ -x ]: a bare name like "python3" lives on PATH, and
# testing it as a file path only ever finds ./python3, which never exists
usable() {
    local py
    py="$(command -v "$1" 2>/dev/null)" || return 1
    "$py" -c "import gi" >/dev/null 2>&1
}

PY=""
for candidate in ".venv/bin/python" "$MEWMORI_PYTHON" "python3"; do
    [ -n "$candidate" ] || continue
    if usable "$candidate"; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
    echo "Не нашёл питона с PyGObject. Поставь: sudo apt install python3-gi python3-gi-cairo" >&2
    exit 1
fi

exec "$PY" -m mewmori "$@"
