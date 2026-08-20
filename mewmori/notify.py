"""Desktop notifications everyone else is sending, seen from the side.

A notification is a method call to org.freedesktop.Notifications addressed to
the panel, not a broadcast signal, so it cannot simply be subscribed to: the
bus has to be monitored. `dbus-monitor` already is that monitor, and a monitor
connection is not allowed to do anything else — which is exactly why this is a
subprocess instead of a second Gio connection inside the cat.

Nothing is delivered on arrival. Notes land in `pending` and the caller decides
when interrupting is fair, so a message that came in mid-thought gets mentioned
at the next pause instead of over the top of whatever is being written.
"""
from __future__ import annotations

import os
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass, field

# the cat has nothing to tell itself, and Spotify's track popups are already
# handled properly through MPRIS in music.py
IGNORE = {"мяумори", "mewmori", "spotify"}
URGENT = 2                      # freedesktop "critical"
IDLE_FLUSH = 0.3                # s of silence that means the message ended
HEADER = re.compile(r"^(method call|signal|error|method return)\s")
STRING = re.compile(r'^\s*string "(.*)"\s*$')


@dataclass(frozen=True)
class Note:
    app: str = ""
    summary: str = ""
    body: str = ""
    urgency: int = 1
    at: float = field(default_factory=time.time)

    @property
    def urgent(self) -> bool:
        return self.urgency >= URGENT

    def __str__(self):
        who = self.app or "что-то"
        what = " — ".join(x for x in (self.summary, self.body) if x)
        return f"{who}: {what[:220]}"


def parse_block(lines) -> Note | None:
    """One `member=Notify` block of dbus-monitor output.

    The call signature is (app_name, replaces_id, icon, summary, body, actions,
    hints, timeout), so the first four *strings* are app, icon, summary, body —
    the id is an integer and does not count. Everything after that is the
    actions array and the hints dict, which is where urgency lives.
    """
    strings, urgency, want_urgency = [], 1, False
    pending = None                       # a string that spans several lines
    for line in lines:
        if pending is not None:
            pending += "\n" + line
            if line.rstrip().endswith('"'):
                strings.append(pending.strip().rstrip('"'))
                pending = None
            continue
        m = STRING.match(line)
        if m:
            value = m.group(1)
            if value == "urgency":
                want_urgency = True
            elif len(strings) < 4:
                strings.append(value)
            continue
        if line.lstrip().startswith('string "') and not line.rstrip().endswith('"'):
            pending = line.split('string "', 1)[1]
            continue
        if want_urgency and "byte" in line:
            digits = re.search(r"byte\s+(\d+)", line)
            if digits:
                urgency = int(digits.group(1))
            want_urgency = False
    if len(strings) < 4:
        return None
    app, _icon, summary, body = strings[:4]
    if app.strip().lower() in IGNORE or not (summary.strip() or body.strip()):
        return None
    return Note(app=app.strip(), summary=summary.strip(), body=body.strip(),
                urgency=urgency)


class Listener:
    """Collects notifications into `pending` until someone drains them.

    Runs a reader thread; `pending` is only ever appended to there and only
    ever emptied by drain(), and list append/clear are atomic under the GIL, so
    no lock is needed for two operations that simple.
    """

    def __init__(self, limit: int = 12, start: bool = True):
        self.pending: list[Note] = []
        self.limit = limit
        self.error = ""
        self.proc = None
        self._thread = None
        if start:
            self.start()

    def start(self):
        """Idempotent, so the settings switch can be flipped either way twice."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        rule = "interface='org.freedesktop.Notifications',member='Notify'"
        try:
            # bufsize=0 and raw reads on purpose: select() reports the *pipe*,
            # while a buffered readline would have already pulled the rest of
            # the message into Python's own buffer, where select cannot see it
            # and the block would sit half-read forever
            self.proc = subprocess.Popen(
                ["dbus-monitor", "--session", rule],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0, stdin=subprocess.DEVNULL)
        except OSError as e:
            self.error = str(e)          # no dbus-monitor: the cat lives on
            return
        # a block is only known to be complete when something else follows it,
        # and with a match rule this narrow the next message may be an hour
        # away — so a pause in the output ends the block instead
        fd = self.proc.stdout.fileno()
        buf, block, inside = b"", [], False
        while True:
            ready, _, _ = select.select([fd], [], [], IDLE_FLUSH)
            if not ready:
                if inside:
                    self._add(parse_block(block))
                    block, inside = [], False
                if self.proc.poll() is not None:
                    return
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            *lines, buf = (buf + chunk).split(b"\n")
            for raw in lines:
                line = raw.decode("utf8", "replace")
                if HEADER.match(line):
                    if inside:
                        self._add(parse_block(block))
                    block, inside = [], ("member=Notify" in line
                                         and line.startswith("method call"))
                    continue
                if inside:
                    block.append(line)
        if inside:
            self._add(parse_block(block))

    def _add(self, note):
        if note is None:
            return
        self.pending = (self.pending + [note])[-self.limit:]

    def waiting(self) -> int:
        return len(self.pending)

    def oldest_age(self, now: float) -> float:
        return now - self.pending[0].at if self.pending else 0.0

    def has_urgent(self) -> bool:
        return any(n.urgent for n in self.pending)

    def drain(self) -> list[Note]:
        notes, self.pending = self.pending, []
        return notes

    def stop(self):
        """Kills dbus-monitor; the reader thread ends when its pipe closes."""
        if self.proc:
            self.proc.terminate()
            self.proc = None
        self.pending = []       # anything held was never worth saying later


if __name__ == "__main__":       # python3 -m mewmori.notify — watch for 30s
    listener = Listener()
    for _ in range(30):
        time.sleep(1)
        for n in listener.drain():
            print(("СРОЧНО " if n.urgent else "") + str(n))
    listener.stop()
