"""The settings window.

Split from app.py because that file is the cat, and this is a form. Everything
here writes through config.save() and then calls back into the running cat, so
a toggle takes effect immediately — the only settings that wait for a restart
are the ones that decide how the window itself was built, and those say so.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import chat, config, ears, health, keys, knowledge, telegram, voice  # noqa: E402

# (key, label, hint) for the plain on/off half of the form
WATCHES = (
    ("watch_screen", "Смотреть на экран",
     "Раз в 7–18 минут кот делает снимок экрана и смотрит, чем ты занят. "
     "Снимок никуда не уходит и на диск не пишется."),
    ("comment_screen", "Говорить про увиденное",
     "Без этого увиденное только записывается в дневник и вслух не звучит — "
     "самое интересное, что кот знает, оказывается единственным, о чём он "
     "молчит."),
    ("watch_tabs", "Видеть вкладки браузера",
     "Читает файл сессии Firefox. В сеть не ходит."),
    ("watch_notifications", "Слышать уведомления",
     "Копит уведомления и отдаёт их на паузе, а не поверх твоей работы."),
    ("watch_claude", "Следить за Claude Code",
     "Скажет, когда Клод ждёт ответа или закончил работу."),
    ("watch_hardware", "Следить за железом",
     "Диск, память, нагрузка — здесь и на машинах из hosts.json."),
    ("watch_music", "Слушать музыку", "Комментирует то, что играет в Spotify."),
    ("watch_telegram", "Доступ к телеграму",
     "Кот сможет читать диалоги и отправлять сообщения от твоего имени. "
     "Вход — ниже, в разделе «Телеграм»."),
)


class Window(Gtk.Window):
    """One column of rows. `cat` is the live Cat, so changes land at once."""

    def __init__(self, cat):
        super().__init__(title="Настройки Мяумори")
        self.cat = cat
        self.set_keep_above(True)
        self.set_default_size(430, -1)
        self.set_position(Gtk.WindowPosition.CENTER)
        # the whole form is taller than a 1080p screen, so it scrolls rather
        # than growing a window whose Close button lands below the desktop
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(720)
        self.add(scroll)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(14)
        scroll.add(box)

        box.pack_start(self._heading("Хозяин"), False, False, 0)
        self.name_entry = Gtk.Entry(text=config.owner())
        self.name_entry.set_placeholder_text("как к тебе обращаться")
        self.name_entry.connect("changed", self._owner_changed)
        box.pack_start(self._row("Имя", self.name_entry), False, False, 0)

        self.gender = Gtk.ComboBoxText()
        for value in ("мужской", "женский"):
            self.gender.append(value, value)
        self.gender.set_active_id(config.owner_gender())
        self.gender.connect("changed", self._owner_changed)
        box.pack_start(self._row(
            "Род", self.gender,
            "В русском языке род слышен в каждом глаголе прошедшего времени: "
            "«ты пришёл» или «ты пришла»."), False, False, 0)

        box.pack_start(self._heading("Что кот замечает"), False, False, 0)
        self.switches = {}          # so the login dialog can flip one back on
        for key, label, hint in WATCHES:
            switch = Gtk.Switch(active=bool(config.get(key)))
            self.switches[key] = switch
            switch.set_halign(Gtk.Align.END)
            if key == "watch_screen" and not cat.model_vision:
                switch.set_sensitive(False)
                hint = "Нет модели со зрением — ollama не отдала ни одной."
            switch.connect("notify::active", self._watch_changed, key)
            box.pack_start(self._row(label, switch, hint), False, False, 0)

        box.pack_start(self._heading("Разговор"), False, False, 0)
        gap = Gtk.SpinButton.new_with_range(15, 600, 15)
        gap.set_value(float(config.get("chatter_gap")))
        gap.connect("value-changed", self._gap_changed)
        box.pack_start(self._row(
            "Пауза между репликами, с", gap,
            "Нижняя граница для всего, что кот говорит по своей инициативе. "
            "Ответы на твои вопросы её не ждут."), False, False, 0)

        self.remember = Gtk.Switch(active=bool(config.get("remember_session")))
        self.remember.set_halign(Gtk.Align.END)
        self.remember.connect("notify::active", self._remember_changed)
        box.pack_start(self._row(
            "Помнить разговор между запусками", self.remember,
            "Последние несколько реплик переживают перезапуск, "
            "если с тех пор прошло меньше 12 часов."), False, False, 0)

        forget = Gtk.Button(label="Забыть разговор")
        forget.connect("clicked", self._forget)
        box.pack_start(self._row("", forget), False, False, 0)

        box.pack_start(self._heading("Картотека"), False, False, 0)
        self.cards_label = Gtk.Label(xalign=1)
        self._refresh_cards()
        box.pack_start(self._row("Что кот помнит", self.cards_label,
                                 "Раз в сутки, пока тебя нет, кот выжимает из "
                                 "дневника факты, которые стоит помнить долго, "
                                 "и складывает их по темам. Папка открывается "
                                 "в Obsidian как настоящий граф — там же их "
                                 "можно править руками."), False, False, 0)

        self.forget_entry = Gtk.Entry(placeholder_text="что забыть…")
        self.forget_entry.connect("activate", self._forget_fact)
        drop = Gtk.Button(label="Забыть")
        drop.connect("clicked", self._forget_fact)
        drop_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        drop_row.pack_start(self.forget_entry, True, True, 0)
        drop_row.pack_start(drop, False, False, 0)
        box.pack_start(self._row("Забыть про", drop_row,
                                 "Удалит все факты, где встречается эта "
                                 "строка. Совсем, из файлов."), False, False, 0)

        open_cards = Gtk.Button(label="Открыть папку")
        open_cards.connect("clicked", lambda *_: Gtk.show_uri_on_window(
            None, f"file://{knowledge.KNOWLEDGE}", Gdk.CURRENT_TIME))
        box.pack_start(self._row("", open_cards), False, False, 0)

        box.pack_start(self._heading("Модель"), False, False, 0)
        combo = Gtk.ComboBoxText()
        combo.append("", "— выбрать самому —")
        for name in chat.available():
            combo.append(name, name)
        combo.set_active_id(cat.model if cat.model in chat.available() else "")
        combo.connect("changed", self._model_changed)
        box.pack_start(self._row(
            "Чем думает", combo,
            "Одна модель на всё. Сильно ужатая (iq2) путает имена — Kanye West "
            "у неё выходит «Кенни»; четырёхбитная этого не делает, но отвечает "
            "медленнее, если не влезает в видеопамять целиком."),
            False, False, 0)

        box.pack_start(self._heading("Голос: диктовка и команды"), False, False, 0)
        stack_why = ears.available() or keys.available()
        self.voice_on = Gtk.Switch(active=bool(config.get("voice_enabled")))
        self.voice_on.set_halign(Gtk.Align.END)
        self.voice_on.set_sensitive(not stack_why)
        self.voice_on.connect("notify::active", self._voice_toggled)
        box.pack_start(self._row(
            "Слушать клавишу и имя", self.voice_on,
            stack_why or ("Зажми клавишу — говори — отпусти, текст печатается "
                          "туда, где курсор. Два быстрых нажатия подряд — кот "
                          "поправит раскладку и опечатки в поле.")),
            False, False, 0)

        self.dictate_key = Gtk.Button(label=keys.label(config.get("dictate_key")))
        self.dictate_key.connect("clicked", self._grab_key)
        self.dictate_key.set_sensitive(not stack_why)
        box.pack_start(self._row("Клавиша диктовки", self.dictate_key,
                                 "Нажми кнопку, потом нужную клавишу."),
                       False, False, 0)

        self.words = Gtk.Entry(text=", ".join(config.get("wake_words") or []))
        self.words.set_width_chars(22)
        self.words.connect("changed", self._words_changed)
        box.pack_start(self._row(
            "Имена, на которые отзывается", self.words,
            "Через запятую. Совпадение по целым словам, поэтому «кот» не "
            "срабатывает на «который» и «скотч». Можно сказать всё одним "
            "духом: «кот, включи музыку»."), False, False, 0)

        box.pack_start(self._heading("Голос: подтверждение"), False, False, 0)
        self.voice_sw = Gtk.Switch(active=bool(config.get("voice_confirm")))
        self.voice_sw.set_halign(Gtk.Align.END)
        self.voice_sw.connect("notify::active", lambda w, _p: config.save(
            {"voice_confirm": w.get_active()}))
        box.pack_start(self._row(
            "Спрашивать «отправлять?» голосом", self.voice_sw,
            "Кот покажет черновик в пузыре и послушает 4 секунды. "
            "Всё, что не похоже на «да», считается отказом. "
            "Без микрофона или без whisper — те же два вопроса кнопками."),
            False, False, 0)

        why = voice.available()
        state = ("слышу: " + Path(voice.python_path()).parent.parent.name
                 + ", модель " + Path(voice.model_path()).name) if not why else why
        box.pack_start(self._row("Чем слушает", Gtk.Label(label=state, xalign=1),
                                 f"Переопределяется полями voice_python и "
                                 f"voice_model в {config.PATH}"), False, False, 0)

        box.pack_start(self._heading("Другие машины"), False, False, 0)
        box.pack_start(self._machines(), False, False, 0)

        box.pack_start(self._heading("Телеграм"), False, False, 0)
        box.pack_start(self._telegram(), False, False, 0)

        box.pack_start(self._heading("Внешность"), False, False, 0)
        height = Gtk.SpinButton.new_with_range(60, 260, 10)
        height.set_value(float(config.get("height")))
        height.connect("value-changed",
                       lambda w: config.save({"height": int(w.get_value())}))
        box.pack_start(self._row(
            "Рост кота, px", height,
            "Применится при следующем запуске: от роста считается вся геометрия "
            "окна."), False, False, 0)

        close = Gtk.Button(label="Закрыть")
        close.connect("clicked", lambda *_: self.destroy())
        close.set_margin_top(8)
        box.pack_start(close, False, False, 0)

        self.show_all()

    # -- layout helpers ---------------------------------------------------
    def _heading(self, text):
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{text}</b>")
        label.set_margin_top(12)
        return label

    def _row(self, label, widget, hint=""):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        text = Gtk.Label(label=label, xalign=0)
        line.pack_start(text, True, True, 0)
        widget.set_valign(Gtk.Align.CENTER)
        line.pack_end(widget, False, False, 0)
        row.pack_start(line, False, False, 0)
        if hint:
            note = Gtk.Label(xalign=0, wrap=True)
            # the hint is markup, and a stray & or < in it would blow up Pango
            note.set_markup(f"<small><i>{GLib.markup_escape_text(hint)}</i></small>")
            note.set_margin_bottom(4)
            row.pack_start(note, False, False, 0)
        return row

    # -- changes ----------------------------------------------------------
    def _owner_changed(self, *_):
        config.save({"owner": self.name_entry.get_text().strip(),
                     "owner_gender": self.gender.get_active_id() or "мужской"})
        # the character is rebuilt per reply, so nothing else has to happen

    def _watch_changed(self, switch, _param, key):
        config.save({key: switch.get_active()})
        self.cat.apply_settings()

    def _gap_changed(self, spin):
        config.save({"chatter_gap": float(spin.get_value())})
        self.cat.apply_settings()

    def _remember_changed(self, switch, _param):
        config.save({"remember_session": switch.get_active()})

    def _forget(self, _button):
        from . import memory
        memory.forget_session()
        self.cat.history.clear()
        self.cat.say("забыл, о чём мы говорили")

    def _model_changed(self, combo):
        config.save({"model": combo.get_active_id() or ""})
        self.cat.apply_settings()

    def _refresh_cards(self):
        facts = knowledge.everything()
        weeks = len(list(knowledge.DIGESTS.glob("*.md"))) if knowledge.DIGESTS.exists() else 0
        self.cards_label.set_text(
            f"{len(facts)} фактов, {len(knowledge.subjects())} тем"
            + (f", {weeks} сжатых недель" if weeks else ""))

    def _forget_fact(self, _widget):
        needle = self.forget_entry.get_text().strip()
        if not needle:
            return
        gone = knowledge.forget(needle)
        self.forget_entry.set_text("")
        self._refresh_cards()
        self.cat.say(f"забыл {gone} записей про «{needle[:24]}»" if gone
                     else f"а я и не помнил про «{needle[:24]}»")

    # -- other machines, inline ---------------------------------------------
    def _machines(self):
        """List, editor and connection test, all in the settings page.

        A separate window for four fields was one window too many; the editing
        fields simply stay hidden until there is something to edit.
        """
        self.hosts = health.load_hosts()
        self.editing = None          # index being edited, or -1 for a new one

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.host_list = Gtk.ListBox()
        self.host_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        frame = Gtk.ScrolledWindow()
        frame.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        frame.set_min_content_height(70)
        frame.set_max_content_height(130)
        frame.set_propagate_natural_height(True)
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.host_list)
        wrap.pack_start(frame, False, False, 0)

        self.host_status = Gtk.Label(xalign=0, wrap=True)
        self.host_status.set_markup("<small> </small>")
        wrap.pack_start(self.host_status, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, handler in (("Добавить", self._host_add),
                               ("Изменить", self._host_edit),
                               ("Проверить", self._host_check),
                               ("Удалить", self._host_remove)):
            button = Gtk.Button(label=label)
            button.connect("clicked", handler)
            buttons.pack_start(button, True, True, 0)
        wrap.pack_start(buttons, False, False, 0)

        # the editor, hidden until Add or Edit is pressed
        self.host_form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.f_name = Gtk.Entry(placeholder_text="как кот её называет")
        self.f_host = Gtk.Entry(placeholder_text="veyron или artem@example.com")
        self.f_port = Gtk.Entry(placeholder_text="22")
        self.f_pass = Gtk.Entry(visibility=False,
                                placeholder_text="только если ключа нет")
        self.f_key = Gtk.FileChooserButton(title="Ключ ssh")
        self.f_key.set_current_folder(str(Path.home() / ".ssh"))
        clear_key = Gtk.Button(label="×")
        clear_key.set_tooltip_text("ключ по умолчанию")
        clear_key.connect("clicked", lambda *_: self.f_key.unselect_all())
        key_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        key_row.pack_start(self.f_key, True, True, 0)
        key_row.pack_start(clear_key, False, False, 0)

        for label, widget in (("Название", self.f_name), ("Хост", self.f_host),
                              ("Порт", self.f_port), ("Ключ", key_row),
                              ("Пароль", self.f_pass)):
            self.host_form.pack_start(self._row(label, widget), False, False, 0)
        self.host_form.pack_start(self._hint(
            "Хост из ~/.ssh/config достаточно назвать по имени. Пароль лежит "
            "в hosts.json открытым (файл 0600) и уходит в ssh через "
            "SSH_ASKPASS, не в аргументах команды — ключ надёжнее."),
            False, False, 0)
        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel = Gtk.Button(label="Отмена")
        cancel.connect("clicked", lambda *_: self._host_form_hide())
        save = Gtk.Button(label="Сохранить")
        save.get_style_context().add_class("suggested-action")
        save.connect("clicked", self._host_save)
        save_row.pack_end(save, False, False, 0)
        save_row.pack_end(cancel, False, False, 0)
        self.host_form.pack_start(save_row, False, False, 0)

        self.host_reveal = Gtk.Revealer()
        self.host_reveal.add(self.host_form)
        wrap.pack_start(self.host_reveal, False, False, 0)

        self._hosts_refill()
        return wrap

    def _hint(self, text):
        label = Gtk.Label(xalign=0, wrap=True)
        label.set_markup(f"<small><i>{GLib.markup_escape_text(text)}</i></small>")
        return label

    def _hosts_refill(self):
        for child in self.host_list.get_children():
            self.host_list.remove(child)
        for spec in self.hosts:
            how = "пароль" if spec.get("password") else "ключ"
            label = Gtk.Label(xalign=0, margin=5)
            label.set_text(f"{spec.get('name') or spec['host']} — "
                           f"{spec['host']} ({how})")
            self.host_list.add(label)
        if not self.hosts:
            empty = Gtk.Label(xalign=0, margin=5)
            empty.set_markup("<small><i>ни одной</i></small>")
            self.host_list.add(empty)
        self.host_list.show_all()

    def _host_index(self):
        row = self.host_list.get_selected_row()
        i = row.get_index() if row else -1
        return i if 0 <= i < len(self.hosts) else -1

    def _host_form_show(self, spec):
        self.f_name.set_text(spec.get("name", ""))
        self.f_host.set_text(spec.get("host", ""))
        self.f_port.set_text(str(spec.get("port", "") or ""))
        self.f_pass.set_text(spec.get("password", ""))
        if spec.get("key"):
            self.f_key.set_filename(os.path.expanduser(spec["key"]))
        else:
            self.f_key.unselect_all()
        self.host_reveal.set_reveal_child(True)
        self.host_form.show_all()

    def _host_form_hide(self):
        self.editing = None
        self.host_reveal.set_reveal_child(False)

    def _host_add(self, _b):
        self.editing = -1
        self._host_form_show({})

    def _host_edit(self, _b):
        i = self._host_index()
        if i < 0:
            self._host_say("сначала выбери машину в списке")
            return
        self.editing = i
        self._host_form_show(self.hosts[i])

    def _host_save(self, _b):
        host = self.f_host.get_text().strip()
        if not host:
            self._host_say("без хоста машины не бывает")
            return
        spec = {"host": host}
        if self.f_name.get_text().strip():
            spec["name"] = self.f_name.get_text().strip()
        if self.f_port.get_text().strip().isdigit():
            spec["port"] = int(self.f_port.get_text().strip())
        chosen = self.f_key.get_filename()
        if chosen:
            spec["key"] = chosen
        if self.f_pass.get_text():
            spec["password"] = self.f_pass.get_text()
        if self.editing is None or self.editing < 0:
            self.hosts.append(spec)
        else:
            self.hosts[self.editing] = spec
        health.save_hosts(self.hosts)
        self._host_form_hide()
        self._hosts_refill()
        self._host_say(f"сохранил {spec.get('name') or spec['host']}")

    def _host_remove(self, _b):
        i = self._host_index()
        if i < 0:
            self._host_say("сначала выбери машину в списке")
            return
        gone = self.hosts.pop(i)
        health.save_hosts(self.hosts)
        self._host_form_hide()
        self._hosts_refill()
        self._host_say(f"убрал {gone.get('name') or gone['host']}")

    def _host_check(self, _b):
        i = self._host_index()
        if i < 0:
            self._host_say("сначала выбери машину в списке")
            return
        spec = self.hosts[i]
        self._host_say("спрашиваю…")

        def work():
            reading = health.remote(spec)
            GLib.idle_add(self._host_say, str(reading))

        threading.Thread(target=work, daemon=True).start()

    def _host_say(self, text):
        self.host_status.set_markup(
            f"<small>{GLib.markup_escape_text(str(text))}</small>")
        return False

    # -- telegram, inline ----------------------------------------------------
    def _telegram(self):
        """One account, set up here; the code and the password go to the cat.

        Typing a login code into a settings form is the least interesting
        possible place for it. The cat asks for both in its own balloon —
        which also means the secret is never in a widget that could be
        screenshotted, focused or read back by anything else.
        """
        saved = telegram.creds()
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.tg_id = Gtk.Entry(text=str(saved.get("api_id", "") or ""),
                               placeholder_text="api_id — только цифры")
        self.tg_hash = Gtk.Entry(text=saved.get("api_hash", ""),
                                 placeholder_text="api_hash")
        self.tg_phone = Gtk.Entry(placeholder_text="+7…")
        for label, widget in (("api_id", self.tg_id), ("api_hash", self.tg_hash),
                              ("Номер телефона", self.tg_phone)):
            wrap.pack_start(self._row(label, widget), False, False, 0)
        wrap.pack_start(self._hint(
            "Пару api_id/api_hash бери на my.telegram.org → API development "
            "tools: она своя для каждого приложения и нужна один раз. "
            "Код из телеграма и облачный пароль кот спросит сам, в пузыре."),
            False, False, 0)

        self.tg_status = Gtk.Label(xalign=0, wrap=True)
        wrap.pack_start(self.tg_status, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.tg_button = Gtk.Button(label="Войти")
        self.tg_button.connect("clicked", self._tg_login)
        row.pack_end(self.tg_button, False, False, 0)
        wrap.pack_start(row, False, False, 0)
        self._tg_say(telegram.available() or "вход выполнен")
        return wrap

    def _tg_say(self, text):
        self.tg_status.set_markup(
            f"<small>{GLib.markup_escape_text(str(text))}</small>")
        return False

    def _tg_login(self, _button):
        why = telegram.available()
        if why.startswith("нет telethon"):
            self._tg_say(why)
            return
        api_id, api_hash = self.tg_id.get_text().strip(), self.tg_hash.get_text().strip()
        if not api_id.isdigit() or not api_hash:
            self._tg_say("api_id — число, api_hash — строка")
            return
        phone = self.tg_phone.get_text().strip()
        if len(phone) < 5:
            self._tg_say("похоже, это не номер")
            return
        telegram.save_creds(int(api_id), api_hash)
        self._tg_say("прошу код…")
        self.tg_button.set_sensitive(False)
        self.login = telegram.Login()
        self.login.send_code(phone, lambda _r, err: GLib.idle_add(
            self._tg_code_sent, err))

    def _tg_code_sent(self, err):
        self.tg_button.set_sensitive(True)
        if err:
            self._tg_say(f"не вышло: {err}")
            return False
        self._tg_say("код отправлен — кот спросит его сам")
        self.cat.prompt("тебе пришёл код, проверь телеграм", self._tg_got_code)
        return False

    def _tg_got_code(self, code):
        if not code:
            self._tg_say("вход отменён")
            return
        self._tg_say("проверяю код…")
        self.login.sign_in(code, lambda state, err: GLib.idle_add(
            self._tg_signed, state, err))

    def _tg_signed(self, state, err):
        if err:
            self._tg_say(f"не вышло: {err}")
            self.cat.say("код не подошёл")
            return False
        if state == "password":
            self._tg_say("нужен облачный пароль — кот спросит")
            self.cat.prompt("а тут ещё и двухэтапная. пароль?",
                            self._tg_got_password, secret=True)
            return False
        self._tg_done()
        return False

    def _tg_got_password(self, password):
        if not password:
            self._tg_say("вход отменён")
            return
        self._tg_say("проверяю пароль…")
        self.login.sign_in_password(password, lambda _s, err: GLib.idle_add(
            self._tg_signed, "ok", err))

    def _tg_done(self):
        who = getattr(self.login, "me", "") or "аккаунт"
        self._tg_say(f"вошёл как {who}. Сессия в {telegram.SESSION}.session — "
                     f"это полный доступ, никуда её не выкладывай.")
        self.cat.say(f"вошёл в телеграм как {who}", secs=8)
        # logging in is the only reason to do this, so turn the feature on
        # rather than making the owner hunt for the switch afterwards
        config.save({"watch_telegram": True})
        self.switches["watch_telegram"].set_active(True)
        self.cat.apply_settings()
        if self.login:
            self.login.stop()
            self.login = None

    def _voice_toggled(self, switch, _param):
        config.save({"voice_enabled": switch.get_active()})
        self.cat.apply_settings()

    def _words_changed(self, entry):
        words = [w.strip().lower() for w in entry.get_text().split(",") if w.strip()]
        config.save({"wake_words": words or ["мяумори"]})
        self.cat.apply_settings()

    def _grab_key(self, button):
        """Listens for one key press and stores whatever it was.

        Runs on a thread: keys.capture blocks, and blocking the GTK loop here
        would freeze the very window the owner is looking at.
        """
        button.set_label("нажми клавишу…")
        button.set_sensitive(False)

        def work():
            found = keys.capture()
            GLib.idle_add(done, found)

        def done(found):
            button.set_sensitive(True)
            if found.get("id"):
                config.save({"dictate_key": found["id"]})
                self.cat.apply_settings()
            button.set_label(keys.label(config.get("dictate_key")))
            return False

        threading.Thread(target=work, daemon=True).start()
