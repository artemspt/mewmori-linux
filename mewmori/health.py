"""Disk, memory, load and battery — on this machine and on other machines.

A remote host is the same three files read over one ssh round trip, so both
sides feed the identical parser: `_parse` is the only place that knows what a
reading means, and `local()`/`remote()` differ only in how they fetch the text.

Auth is whatever ssh already does. Hosts listed in ~/.ssh/config with a key
need no configuration here at all — just the name. A password host stores its
password in hosts.json, which is written 0600 and handed to ssh through
SSH_ASKPASS rather than a command line, so it never shows up in /proc. A key is
still the better idea; the password path exists because not every box has one.

No GTK, no third-party packages: ssh is a subprocess and everything else is
/proc.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mewmori"
HOSTS = CONFIG / "hosts.json"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mewmori"
ASKPASS = CACHE / "askpass.sh"

SEP = "@@"
# one command, one round trip: anything more chatty would mean several ssh
# handshakes per poll for a machine that is only being glanced at
PROBE = (
    f"cat /proc/meminfo; echo {SEP}; cat /proc/loadavg; echo {SEP}; nproc; "
    f'echo {SEP}; df -Pk "$HOME" | tail -1; echo {SEP}; '
    f"cat /sys/class/power_supply/BAT*/capacity 2>/dev/null; echo {SEP}; "
    f"cat /sys/class/power_supply/BAT*/status 2>/dev/null"
)

DISK_PCT = 8.0        # % free below which the disk counts as full
DISK_GB = 5.0         # ...or this many GB, whichever trips first
MEM_PCT = 8.0         # % of RAM still available
SWAP_PCT = 60.0       # % of swap in use — the machine is thrashing
LOAD_PER_CPU = 2.5    # load average per core
BATTERY_PCT = 15.0
RENOTIFY = 1800.0     # s before the same complaint is worth repeating


@dataclass(frozen=True)
class Reading:
    name: str = ""
    disk_free_pct: float = 100.0
    disk_free_gb: float = 999.0
    mem_free_pct: float = 100.0
    swap_used_pct: float = 0.0
    load1: float = 0.0
    cpus: int = 1
    battery: int = -1          # -1 = no battery on this machine
    charging: bool = True
    error: str = ""

    def __str__(self):
        if self.error:
            return f"{self.name}: не отвечает ({self.error})"
        bits = [f"диск {self.disk_free_gb:.0f} ГБ свободно ({self.disk_free_pct:.0f}%)",
                f"память {self.mem_free_pct:.0f}% свободно",
                f"загрузка {self.load1:.1f} на {self.cpus} ядер"]
        if self.swap_used_pct > 1:
            bits.append(f"swap занят на {self.swap_used_pct:.0f}%")
        if self.battery >= 0:
            bits.append(f"батарея {self.battery}%")
        return f"{self.name}: " + ", ".join(bits)


def _num(text: str, key: str) -> float:
    """One value out of /proc/meminfo, in kB."""
    for line in text.splitlines():
        if line.startswith(key):
            return float(line.split()[1])
    return 0.0


def _parse(name: str, blob: str) -> Reading:
    parts = blob.split(f"\n{SEP}\n") if f"\n{SEP}\n" in blob else blob.split(SEP)
    parts = [p.strip() for p in parts] + [""] * 6
    meminfo, loadavg, nproc, dfline, battery, status = parts[:6]

    total = _num(meminfo, "MemTotal:")
    avail = _num(meminfo, "MemAvailable:")
    swap_total = _num(meminfo, "SwapTotal:")
    swap_free = _num(meminfo, "SwapFree:")
    fields = dfline.split()
    # df -Pk: filesystem, 1k-blocks, used, available, capacity, mount
    free_kb = float(fields[3]) if len(fields) >= 4 else 0.0
    size_kb = float(fields[1]) if len(fields) >= 4 else 0.0

    return Reading(
        name=name,
        disk_free_pct=100.0 * free_kb / size_kb if size_kb else 100.0,
        disk_free_gb=free_kb / 1048576.0,
        mem_free_pct=100.0 * avail / total if total else 100.0,
        swap_used_pct=100.0 * (swap_total - swap_free) / swap_total if swap_total else 0.0,
        load1=float(loadavg.split()[0]) if loadavg else 0.0,
        cpus=int(nproc) if nproc.isdigit() else 1,
        battery=int(battery.splitlines()[0]) if battery[:1].isdigit() else -1,
        charging="Discharging" not in status,
    )


def local(name: str = "тут") -> Reading:
    """This machine, read straight off /proc — no subprocess at all."""
    try:
        du = shutil.disk_usage(Path.home())
        bat = sorted(Path("/sys/class/power_supply").glob("BAT*"))
        blob = SEP.join([
            Path("/proc/meminfo").read_text(),
            Path("/proc/loadavg").read_text(),
            str(os.cpu_count() or 1),
            # fabricated df line so the remote parser is the only parser
            f"local {du.total // 1024} {du.used // 1024} {du.free // 1024} 0% /",
            (bat[0] / "capacity").read_text() if bat else "",
            (bat[0] / "status").read_text() if bat else "",
        ])
        return _parse(name, blob)
    except (OSError, ValueError, IndexError) as e:
        return Reading(name=name, error=str(e))


def _askpass() -> str:
    """A one-line helper ssh can call for the password, instead of a tty.

    OpenSSH refuses to read a password from anywhere but a terminal unless
    SSH_ASKPASS_REQUIRE=force is set, which is why this exists at all. The
    password arrives in the helper's environment, so it is never an argv entry
    that any other user could read out of /proc.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    if not ASKPASS.exists():
        ASKPASS.write_text('#!/bin/sh\nprintf "%s\\n" "$MEWMORI_SSH_PASS"\n')
        ASKPASS.chmod(0o700)
    return str(ASKPASS)


def remote(spec: dict, timeout: float = 15.0) -> Reading:
    """One host over ssh. Blocking — call it from a thread."""
    name = spec.get("name") or spec.get("host", "?")
    host = spec.get("host")
    if not host:
        return Reading(name=name, error="в hosts.json нет поля host")

    args = ["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new"]
    if spec.get("port"):
        args += ["-p", str(spec["port"])]
    if spec.get("key"):
        args += ["-o", "IdentitiesOnly=yes", "-i", os.path.expanduser(spec["key"])]
    env = dict(os.environ)
    if spec.get("password"):
        # keyboard-interactive first: some boxes offer it instead of `password`
        args += ["-o", "PubkeyAuthentication=no",
                 "-o", "PreferredAuthentications=password,keyboard-interactive",
                 "-o", "NumberOfPasswordPrompts=1"]
        env.update(SSH_ASKPASS=_askpass(), SSH_ASKPASS_REQUIRE="force",
                   MEWMORI_SSH_PASS=spec["password"], DISPLAY=env.get("DISPLAY", ":0"))
    else:
        args += ["-o", "BatchMode=yes"]      # never hang waiting for a prompt
    args += [host, PROBE]

    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return Reading(name=name, error="таймаут")
    except OSError as e:
        return Reading(name=name, error=str(e))
    # a machine with no battery makes the last `cat` fail, so a non-zero exit
    # with output is normal; only silence means the connection itself failed
    if p.returncode != 0 and not p.stdout.strip():
        err = (p.stderr.strip() or "ssh вернул код %d" % p.returncode).splitlines()[-1]
        return Reading(name=name, error=err[:80])
    try:
        return _parse(name, p.stdout)
    except (ValueError, IndexError) as e:
        return Reading(name=name, error=f"не разобрал ответ: {e}")


def load_hosts() -> list[dict]:
    """Other machines to keep an eye on. Missing file just means none."""
    try:
        data = json.loads(HOSTS.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [h for h in data if isinstance(h, dict) and h.get("host")] if isinstance(data, list) else []


def save_hosts(hosts: list[dict]) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    HOSTS.write_text(json.dumps(hosts, ensure_ascii=False, indent=2), encoding="utf8")
    HOSTS.chmod(0o600)          # it may hold a password


def problems(r: Reading) -> list[tuple[str, str]]:
    """(key, plain sentence) for everything currently wrong with one machine.

    The key identifies the complaint so the same one is not made twice; the
    sentence is what gets handed to the model to say in its own words.
    """
    if r.error:
        return [(f"{r.name}:down", f"машина «{r.name}» не отвечает: {r.error}")]
    out = []
    where = "" if r.name == "тут" else f"на машине «{r.name}» "
    if r.disk_free_pct < DISK_PCT or r.disk_free_gb < DISK_GB:
        out.append((f"{r.name}:disk",
                    f"{where}кончается место на диске: осталось "
                    f"{r.disk_free_gb:.1f} ГБ ({r.disk_free_pct:.0f}%)"))
    if r.mem_free_pct < MEM_PCT:
        out.append((f"{r.name}:mem",
                    f"{where}почти кончилась оперативная память: свободно "
                    f"{r.mem_free_pct:.0f}%"))
    if r.swap_used_pct > SWAP_PCT:
        out.append((f"{r.name}:swap",
                    f"{where}система ушла в swap на {r.swap_used_pct:.0f}% — всё будет тормозить"))
    if r.load1 > LOAD_PER_CPU * r.cpus:
        out.append((f"{r.name}:load",
                    f"{where}загрузка {r.load1:.1f} при {r.cpus} ядрах — машина перегружена"))
    if 0 <= r.battery < BATTERY_PCT and not r.charging:
        out.append((f"{r.name}:bat", f"{where}батарея на {r.battery}% и не заряжается"))
    return out


class Watcher:
    """Polls this machine and the configured ones, and reports what is new.

    A complaint fires once, then stays quiet until it has cleared *and* enough
    time has passed — a value sitting just under a threshold would otherwise
    turn the cat into a smoke alarm.
    """

    def __init__(self, hosts=None):
        self.hosts = load_hosts() if hosts is None else hosts
        self.last = {}          # name -> Reading, for context in other prompts
        self._fired = {}        # key -> monotonic time it was last reported
        self._busy = False

    def news(self, reading, now) -> list[tuple[str, str]]:
        """What is newly wrong with one machine. Caller's thread, always the
        same one: the bookkeeping below is read-modify-write and is not locked."""
        self.last[reading.name] = reading
        found = problems(reading)
        keys = {k for k, _ in found}
        for key in list(self._fired):
            if key.startswith(reading.name + ":") and key not in keys:
                del self._fired[key]        # cleared: it may be reported again
        out = []
        for key, text in found:
            if now - self._fired.get(key, -RENOTIFY) >= RENOTIFY:
                self._fired[key] = now
                out.append((key, text))
        return out

    def check_local(self, now) -> list[tuple[str, str]]:
        return self.news(local(), now)

    def fetch_remote(self, on_readings) -> None:
        """ssh blocks for seconds; the frame loop must not.

        The thread only fetches. Deciding what is *news* stays with the caller,
        so the one place that tracks which complaints have been made is only
        ever touched from one thread.
        """
        if self._busy or not self.hosts:
            return
        self._busy = True

        def work():
            readings = [remote(spec) for spec in self.hosts]
            self._busy = False
            on_readings(readings)

        threading.Thread(target=work, daemon=True).start()

    def summary(self, limit: int = 0) -> str:
        """One line per machine. limit caps it for the speech balloon, which is
        300 px wide and clips whatever does not fit above the cat's head."""
        rows = list(self.last.values())
        shown = rows[:limit] if limit else rows
        text = "\n".join(str(r) for r in shown)
        if len(rows) > len(shown):
            text += f"\n…и ещё {len(rows) - len(shown)}"
        return text


if __name__ == "__main__":       # python3 -m mewmori.health — read everything once
    r = local()
    print(r)
    for key, text in problems(r):
        print("  !", key, "—", text)
    for spec in load_hosts():
        rr = remote(spec)
        print(rr)
        for key, text in problems(rr):
            print("  !", key, "—", text)
