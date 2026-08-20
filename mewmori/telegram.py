"""Telegram as the owner's own account, not as notifications.

Notifications tell you a message arrived. This lets the cat read the dialogue
and answer in it, which needs MTProto and therefore Telethon — the one
optional dependency in the project. Nothing else here requires it: if Telethon
is missing the feature reports why and the rest of the cat carries on.

    pip install --user telethon

**Logging in is the owner's job, not the cat's.** The phone number, the code
Telegram sends and the two-factor password are typed by a human into their own
terminal:

    python3 -m mewmori.telegram login

Telethon then writes a session file and nothing ever asks again. The cat only
ever holds that session — it never sees the password and never asks for it.

Telethon is asyncio and GTK is not, so the client lives on one background
thread with one event loop, and every answer is handed back through a
callback the caller marshals onto its own loop.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from . import config

CREDS = config.CONFIG / "telegram.json"
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mewmori"
SESSION = DATA / "telegram"          # Telethon appends .session itself
CALL_TIMEOUT = 40.0

# "ответь лонеру что у меня нет таких денег" — the verb and the target are
# matched here, and what to actually say is left to the model. Anything that
# does not look like this is an ordinary remark to the cat and is left alone.
COMMAND = re.compile(
    r"^\s*(?:ответь|ответить|напиши|написать|скажи|передай|отправь)\s+"
    r"(?P<who>[^,]{1,40}?)\s*[,]?\s*"
    r"(?:что|чтобы|про то что|:)\s+"
    r"(?P<what>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# The cat is a courier, not the owner: it must never write as if it were them.
DRAFT_SYSTEM = (
    "Ты — кот по имени Мяумори. Хозяин попросил передать сообщение человеку "
    "по имени {who} в телеграме. Напиши это сообщение.\n"
    "Строго по форме: начни со слов «говорит mewmori», дальше передай смысл "
    "от третьего лица — «хозяин говорит, что …», — и закончи эмодзи кошки.\n"
    "Одно-два предложения, вежливо и по-человечески. Ничего не выдумывай "
    "сверх того, что просил хозяин.\n"
    "В ответе — только текст сообщения. Без кавычек, без пояснений, "
    "без markdown."
)


@dataclass(frozen=True)
class Peer:
    id: int
    name: str
    username: str = ""
    is_user: bool = True

    def __str__(self):
        return self.name + (f" (@{self.username})" if self.username else "")


def parse_command(text: str):
    """('лонеру', 'у меня нет таких денег') — or None if this is just chatter."""
    m = COMMAND.match(text or "")
    if not m:
        return None
    who = m.group("who").strip(" ,:")
    what = m.group("what").strip()
    return (who, what) if who and what else None


def creds() -> dict:
    try:
        data = json.loads(CREDS.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if data.get("api_id") and data.get("api_hash") else {}


def save_creds(api_id: int, api_hash: str) -> None:
    config.CONFIG.mkdir(parents=True, exist_ok=True)
    CREDS.write_text(json.dumps({"api_id": int(api_id), "api_hash": api_hash}),
                     encoding="utf8")
    CREDS.chmod(0o600)


def available() -> str:
    """Empty string when Telegram can be used; otherwise why it cannot."""
    try:
        import telethon  # noqa: F401
    except ImportError:
        # not `pip install --user`: Debian marks the system Python
        # externally-managed and refuses, which is why run.sh looks for a venv
        return ("нет telethon — см. requirements.txt: "
                "python3 -m venv --system-site-packages .venv "
                "&& .venv/bin/pip install -r requirements.txt")
    if not creds():
        return "не заданы api_id/api_hash — войди в настройках"
    if not SESSION.with_suffix(".session").exists():
        return "не выполнен вход — войди в настройках"
    return ""


def match(query: str, peers) -> list:
    """Dialogs whose name looks like what the owner typed, best first.

    Russian is inflected — "ответь лонеру" is the dative of "лонер" — so the
    ending is not compared: the shorter of the two is matched as a prefix.
    """
    q = query.strip().lower().lstrip("@")
    if not q:
        return []
    stem = q[:-2] if len(q) > 4 else q      # drop a case ending, roughly
    exact, loose = [], []
    for p in peers:
        name = (p.name or "").lower()
        user = (p.username or "").lower()
        if q == name or q == user:
            exact.append(p)
        elif name.startswith(stem) or user.startswith(stem) or stem in name:
            loose.append(p)
    return exact + loose


class _Client:
    """One Telethon client, on one thread, with one event loop.

    Telethon is asyncio and GTK is not, so the loop lives here and every
    public method takes on_done(result, error) and returns at once. The
    callback runs on a worker thread — a GTK caller must marshal it.

    Both the running cat and the login dialog need exactly this, which is why
    it is a base class rather than a copy in each.
    """

    #: whether an unauthorised session is a problem (it is, except when the
    #: whole point of the client is to authorise one)
    needs_auth = True

    def __init__(self):
        self.error = ""
        self.client = None
        self.loop = None
        self.me = ""
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            from telethon import TelegramClient
        except ImportError as e:
            self.error = str(e)
            self._ready.set()
            return
        c = creds()
        if not c:
            self.error = "не заданы api_id/api_hash"
            self._ready.set()
            return
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        DATA.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(str(SESSION), c["api_id"], c["api_hash"])
        try:
            self.loop.run_until_complete(self.client.connect())
            if self.loop.run_until_complete(self.client.is_user_authorized()):
                who = self.loop.run_until_complete(self.client.get_me())
                self.me = getattr(who, "first_name", "") or ""
            elif self.needs_auth:
                self.error = "вход не выполнен"
        except Exception as e:                       # network, banned key, ...
            self.error = str(e)[:120]
        self._ready.set()
        if not self.error:
            self.loop.run_forever()

    def ready(self, timeout: float = 20.0) -> str:
        """Blocks until the client has connected. Returns "" or the reason not."""
        self._ready.wait(timeout)
        if not self._ready.is_set():
            return "телеграм не отвечает"
        return self.error

    def _call(self, make_coro, on_done):
        def work():
            why = self.ready()
            if why:
                on_done(None, why)
                return
            try:
                fut = asyncio.run_coroutine_threadsafe(make_coro(), self.loop)
                on_done(fut.result(timeout=CALL_TIMEOUT), None)
            except Exception as e:
                on_done(None, str(e)[:160])

        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)


class Login(_Client):
    """The three steps Telegram asks for, one call each.

    The phone number, the code and the cloud password are typed by the owner
    into their own machine and handed straight to Telethon. Nothing here
    stores them, logs them, or puts them anywhere but the request itself —
    what survives is the session file Telethon writes.
    """

    needs_auth = False          # an unauthorised session is the starting point

    def __init__(self):
        super().__init__()
        self.phone = ""

    def already_in(self) -> bool:
        return bool(self.me)

    def send_code(self, phone: str, on_done):
        self.phone = phone.strip()
        self._call(lambda: self.client.send_code_request(self.phone), on_done)

    def sign_in(self, code: str, on_done):
        """on_done(state, error): state is "ok" or "password" when 2FA is on."""
        async def go():
            from telethon.errors import SessionPasswordNeededError
            try:
                who = await self.client.sign_in(self.phone, code.strip())
            except SessionPasswordNeededError:
                return "password"
            self.me = getattr(who, "first_name", "") or ""
            return "ok"

        self._call(go, on_done)

    def sign_in_password(self, password: str, on_done):
        async def go():
            who = await self.client.sign_in(password=password)
            self.me = getattr(who, "first_name", "") or ""
            return "ok"

        self._call(go, on_done)


class Telegram(_Client):
    """The client the cat uses once someone has logged in."""

    # -- what the cat actually needs ---------------------------------------
    def dialogs(self, on_done, limit: int = 120):
        async def go():
            out = []
            async for d in self.client.iter_dialogs(limit=limit):
                entity = d.entity
                out.append(Peer(id=d.id, name=(d.name or "").strip(),
                                username=getattr(entity, "username", "") or "",
                                is_user=bool(d.is_user)))
            return out

        self._call(go, on_done)

    def history(self, peer_id: int, on_done, limit: int = 6):
        """The tail of a conversation, oldest first, as 'кто: что' lines."""
        async def go():
            lines = []
            async for m in self.client.iter_messages(peer_id, limit=limit):
                if not m.message:
                    continue
                who = "хозяин" if m.out else "он"
                lines.append(f"{who}: {m.message.strip()[:200]}")
            return "\n".join(reversed(lines))

        self._call(go, on_done)

    def send(self, peer_id: int, text: str, on_done):
        self._call(lambda: self.client.send_message(peer_id, text), on_done)


# -- the one-time login, run by a human in their own terminal ---------------
def _login():
    try:
        from telethon import TelegramClient
    except ImportError:
        raise SystemExit("Сначала: pip install --user telethon")

    c = creds()
    if not c:
        print("Нужны api_id и api_hash. Возьми их на https://my.telegram.org "
              "→ API development tools (это одна минута и делается один раз).\n")
        api_id = input("api_id: ").strip()
        api_hash = input("api_hash: ").strip()
        if not api_id.isdigit() or not api_hash:
            raise SystemExit("Пусто или не число — ничего не сохранил.")
        save_creds(int(api_id), api_hash)
        c = creds()
        print(f"Сохранил в {CREDS} (права 0600).\n")

    DATA.mkdir(parents=True, exist_ok=True)
    print("Дальше Telethon спросит номер телефона, код из телеграма и, если он "
          "включён, облачный пароль. Всё это вводишь ты, здесь, в своём "
          "терминале — кот их не видит и никуда не отправляет.\n")
    client = TelegramClient(str(SESSION), c["api_id"], c["api_hash"])
    with client:
        me = client.loop.run_until_complete(client.get_me())
        print(f"\nГотово: вошёл как {me.first_name} (@{me.username or 'без ника'}).")
        print(f"Сессия лежит в {SESSION}.session — не выкладывай её никуда.")


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "login":
        _login()
    elif cmd == "status":
        print(available() or "телеграм готов")
    elif cmd == "dialogs":
        why = available()
        if why:
            raise SystemExit(why)
        done = threading.Event()
        result = {}
        tg = Telegram()
        tg.dialogs(lambda peers, err: (result.update(peers=peers, err=err), done.set()))
        done.wait(30)
        if result.get("err"):
            raise SystemExit(result["err"])
        for p in (result.get("peers") or [])[:40]:
            print(f"{p.id:>16}  {p}")
        tg.stop()
    else:
        raise SystemExit("login | status | dialogs")
