"""Интеграционный тест: фейковый IMAP-сервер + фейковый Telegram + настоящий демон и SQLite."""

import json
import select
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from polaris.infra import database
from polaris.modules.mail import daemon, imap, repository, service

RAW = (
    "From: Иван <ivan@example.com>\r\nTo: me@example.com\r\nSubject: =?utf-8?b?0J/RgNC40LLQtdGC?=\r\n"
    "Date: Sun, 25 Oct 2026 12:00:00 +0000\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
    "Текст письма номер {n}\r\n"
)


def make_raw(n: int) -> bytes:
    return RAW.format(n=n).encode("utf-8")


# ─────────────── fake IMAP ───────────────
class FakeImap:
    def __init__(self):
        self.mailbox = {1: make_raw(1), 2: make_raw(2), 3: make_raw(3)}
        self.users = {"me@example.com": "secret"}
        self.logins = 0
        self.idle = False        # объявлять ли поддержку IDLE
        self.push_counter = 0    # счётчик «новое письмо» для push в IDLE


def make_imap_server(state: FakeImap):
    class Handler(socketserver.StreamRequestHandler):
        def send(self, line: bytes):
            self.wfile.write(line + b"\r\n")

        def handle(self):
            self.send(b"* OK [CAPABILITY IMAP4rev1] ready")
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                tag, _, rest = line.decode().strip().partition(" ")
                cmd, _, args = rest.partition(" ")
                cmd = cmd.upper()
                if cmd == "CAPABILITY":
                    self.send(b"* CAPABILITY IMAP4rev1" + (b" IDLE" if state.idle else b""))
                    self.send(f"{tag} OK done".encode())
                elif cmd == "LOGIN":
                    user, _, pw = args.partition(" ")
                    if state.users.get(user.strip('"')) == pw.strip('"'):
                        state.logins += 1
                        self.send(f"{tag} OK logged in".encode())
                    else:
                        self.send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials".encode())
                elif cmd in ("SELECT", "EXAMINE"):
                    self.send(f"* {len(state.mailbox)} EXISTS".encode())
                    self.send(b"* OK [UIDVALIDITY 777] ok")
                    self.send(f"{tag} OK [READ-ONLY] done".encode())
                elif cmd == "IDLE" and state.idle:
                    self.send(b"+ idling")
                    seen = state.push_counter
                    while True:
                        if select.select([self.request], [], [], 0.1)[0]:
                            got = self.rfile.readline()
                            if not got:
                                return
                            if got.strip().upper() == b"DONE":
                                break
                        elif state.push_counter != seen:
                            seen = state.push_counter
                            self.send(f"* {len(state.mailbox)} EXISTS".encode())
                    self.send(f"{tag} OK IDLE terminated".encode())
                elif cmd == "NOOP":
                    self.send(f"{tag} OK NOOP completed".encode())
                elif cmd == "LOGOUT":
                    self.send(b"* BYE"); self.send(f"{tag} OK LOGOUT completed".encode()); return
                elif cmd == "UID":
                    sub, _, rest2 = args.partition(" ")
                    if sub.upper() == "SEARCH":
                        if "ALL" in rest2:
                            uids = sorted(state.mailbox)
                        else:  # UID n:*
                            n = int(rest2.split()[-1].split(":")[0])
                            uids = [u for u in sorted(state.mailbox) if u >= n] or [max(state.mailbox)]
                        self.send(("* SEARCH " + " ".join(map(str, uids))).encode())
                        self.send(f"{tag} OK done".encode())
                    elif sub.upper() == "FETCH":
                        uid = int(rest2.split()[0])
                        raw = state.mailbox[uid]
                        if "RFC822.SIZE" in rest2:
                            self.send(f"* 1 FETCH (UID {uid} RFC822.SIZE {len(raw)})".encode())
                        else:
                            self.wfile.write(f"* 1 FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n")
                        self.send(f"{tag} OK done".encode())
                else:
                    self.send(f"{tag} BAD unknown".encode())

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ─────────────── fake Telegram ───────────────
class FakeTelegram:
    def __init__(self):
        self.requests = []
        self.fail = {}   # chat_id -> (status, description[, retry_after])
        self.rich_unsupported = False


def make_tg_server(state: FakeTelegram):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            state.requests.append((method, body))
            code, out = 200, {"ok": True, "result": {"message_id": 1}}
            failure = state.fail.get(str(body.get("chat_id")))
            if method == "sendRichMessage" and state.rich_unsupported:
                code, out = 400, {"ok": False, "error_code": 400, "description": "Bad Request: can't parse rich message"}
            elif failure:
                code = failure[0]
                out = {"ok": False, "error_code": failure[0], "description": failure[1]}
                if len(failure) > 2:
                    out["parameters"] = {"retry_after": failure[2]}
            data = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def wait_for(cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "t.db")
    repository.ensure_schema()
    imap_state, tg_state = FakeImap(), FakeTelegram()
    imap_srv, tg_srv = make_imap_server(imap_state), make_tg_server(tg_state)
    monkeypatch.setitem(imap.PROVIDERS, "gmail", {"label": "Google", "host": "127.0.0.1", "port": imap_srv.server_address[1], "ssl": False})
    monkeypatch.setenv("TELEGRAM_API_BASE", f"http://127.0.0.1:{tg_srv.server_address[1]}")
    monkeypatch.setattr(daemon, "POLL_INTERVAL", 0.3)
    monkeypatch.setattr(daemon, "RECONCILE_INTERVAL", 0.2)
    monkeypatch.setattr(daemon, "SENDER_INTERVAL", 0.2)
    monkeypatch.setattr(daemon, "SEND_GAP", 0.05)
    monkeypatch.setattr(daemon, "RETRY_DELAYS", (0, 0, 0, 0, 0))
    d = daemon.MailDaemon("TESTTOKEN123")
    yield imap_state, tg_state, d
    d.stop()
    imap_srv.shutdown(); tg_srv.shutdown()


def test_full_flow(env):
    imap_state, tg, d = env
    acc = service.add_account("gmail", "me@example.com", "se cr et", label="Рабочая", verify=False)
    assert "password" not in acc
    imap_state.users["me@example.com"] = "secret"   # пробелы в пароле Google должны вычищаться
    assert repository.get_account(acc["id"], include_secret=True)["password"] == "secret"

    dm = service.add_destination("123456789")
    topic = service.add_destination("https://t.me/c/3281520729/1279", label="Почта-тема")
    assert (topic["chat_id"], topic["thread_id"], topic["kind"]) == ("-1003281520729", 1279, "topic")

    d.start()
    # 1) первый запуск: старые письма показываем в Mini App, но в Telegram НЕ шлём
    assert wait_for(lambda: len(repository.list_messages()) == 3)
    time.sleep(0.8)
    assert tg.requests == [], "старые письма не должны уходить в Telegram"
    assert wait_for(lambda: repository.list_accounts()[0]["last_status"] == "ok")
    assert repository.get_state(acc["id"]) == ("777", 3)

    # 2) новое письмо -> сохранено + доставлено в оба получателя
    imap_state.mailbox[4] = make_raw(4)
    assert wait_for(lambda: len(tg.requests) == 2)
    sent = {(b["chat_id"], b.get("message_thread_id")): b for m, b in tg.requests if m == "sendRichMessage"}
    assert set(sent) == {(123456789, None), (-1003281520729, 1279)}
    md = sent[(123456789, None)]["rich_message"]["markdown"]
    assert "<details><summary>Текст письма</summary>" in md and "номер 4" in md and "**Ящик:** Рабочая" in md
    msgs = repository.list_messages()
    assert msgs[0]["uid"] == 4 and msgs[0]["subject"] == "Привет" and wait_for(lambda: repository.list_messages()[0]["sent_count"] == 2)
    assert repository.get_state(acc["id"])[1] == 4

    # 3) дубликаты не возникают при дальнейшем опросе
    time.sleep(1.0)
    assert len(tg.requests) == 2 and len(repository.list_messages()) == 4


def test_failing_destination_does_not_block_others(env):
    imap_state, tg, d = env
    service.add_account("gmail", "me@example.com", "secret", verify=False)
    service.add_destination("123456789")
    bad = service.add_destination("3281520729/55")
    tg.fail["-1003281520729"] = (400, "Bad Request: message thread not found")
    d.start()
    assert wait_for(lambda: len(repository.list_messages()) == 3)
    imap_state.mailbox[4] = make_raw(4)
    # получатель с ошибкой: 3 попытки и failed; личка — доставлена сразу
    assert wait_for(lambda: repository.get_destination(bad["id"])["failed_count"] == 1)
    dest = repository.get_destination(bad["id"])
    assert dest["last_status"] == "error" and "Тема не найдена" in dest["last_error"]
    assert repository.list_destinations()[0]["sent_count"] == 1
    assert [r for r in tg.requests if r[1]["chat_id"] == -1003281520729].__len__() == 3  # MAX_ATTEMPTS_CONFIG_ERROR


def test_rate_limit_is_waited_not_failed(env):
    imap_state, tg, d = env
    service.add_account("gmail", "me@example.com", "secret", verify=False)
    dm = service.add_destination("123456789")
    tg.fail["123456789"] = (429, "Too Many Requests: retry after 1", 1)
    d.start()
    assert wait_for(lambda: len(repository.list_messages()) == 3)
    imap_state.mailbox[4] = make_raw(4)
    assert wait_for(lambda: len(tg.requests) >= 1)
    assert repository.get_destination(dm["id"])["failed_count"] == 0
    del tg.fail["123456789"]
    assert wait_for(lambda: repository.get_destination(dm["id"])["sent_count"] == 1, timeout=10)


def test_fallback_when_rich_unsupported(env):
    imap_state, tg, d = env
    service.add_account("gmail", "me@example.com", "secret", verify=False)
    dm = service.add_destination("123456789")
    tg.rich_unsupported = True
    d.start()
    assert wait_for(lambda: len(repository.list_messages()) == 3)
    imap_state.mailbox[4] = make_raw(4)
    assert wait_for(lambda: repository.get_destination(dm["id"])["sent_count"] == 1)
    methods = [m for m, _ in tg.requests]
    assert methods == ["sendRichMessage", "sendMessage"]
    assert "<blockquote expandable>" in tg.requests[1][1]["text"]


def test_wrong_password_and_account_management(env):
    imap_state, tg, d = env
    with pytest.raises(service.MailError) as exc:
        service.add_account("gmail", "me@example.com", "wrong")      # проверка логина при добавлении
    assert "wrong" not in str(exc.value) and "Сервер отклонил вход" in str(exc.value)
    with pytest.raises(service.MailError):
        service.add_account("yahoo", "a@b.co", "x")
    with pytest.raises(service.MailError):
        service.add_account("gmail", "not-an-email", "x")

    acc = service.add_account("gmail", "me@example.com", "secret")
    with pytest.raises(service.MailError):
        service.add_account("gmail", "ME@example.com", "secret")      # дубликат без учёта регистра
    d.start()
    assert wait_for(lambda: repository.list_accounts()[0]["last_status"] == "ok")

    service.change_account(acc["id"], enabled=False)                 # выключение останавливает воркер
    assert wait_for(lambda: not d.workers)
    service.change_account(acc["id"], enabled=True)
    assert wait_for(lambda: len(d.workers) == 1)

    service.change_account(acc["id"], password="broken", verify=False)  # неверный пароль -> статус error без утечки пароля
    assert wait_for(lambda: repository.list_accounts()[0]["last_status"] == "error")
    assert "broken" not in repository.list_accounts()[0]["last_error"]
    service.remove_account(acc["id"])
    assert repository.list_messages() == []


def test_destination_test_send(env):
    imap_state, tg, d = env
    dm = service.add_destination("123456789")
    service.test_destination(dm["id"], "TESTTOKEN123")
    assert tg.requests[-1][0] == "sendRichMessage" and repository.get_destination(dm["id"])["last_status"] == "ok"
    tg.fail["123456789"] = (403, "Forbidden: bot can't initiate conversation with a user")
    with pytest.raises(service.MailError) as exc:
        service.test_destination(dm["id"], "TESTTOKEN123")
    assert "нажмите /start" in str(exc.value)
    assert repository.get_destination(dm["id"])["last_status"] == "error"


def test_idle_delivers_instantly_without_polling(env, monkeypatch):
    imap_state, tg, d = env
    imap_state.idle = True
    monkeypatch.setattr(daemon, "POLL_INTERVAL", 1000)   # опрос не должен успеть сработать
    monkeypatch.setattr(daemon, "IDLE_CYCLE", 60)
    service.add_account("gmail", "me@example.com", "secret", verify=False)
    service.add_destination("123456789")
    d.start()
    assert wait_for(lambda: len(repository.list_messages()) == 3)
    time.sleep(0.5)                                      # воркер уже должен сидеть в IDLE
    started = time.time()
    imap_state.mailbox[4] = make_raw(4)
    imap_state.push_counter += 1                         # сервер присылает «* 4 EXISTS»
    assert wait_for(lambda: len(tg.requests) == 1, timeout=5)
    assert time.time() - started < 4, "письмо должно прийти почти мгновенно, а не по таймеру"
    imap_state.mailbox[5] = make_raw(5)                  # и второй раз подряд — IDLE перезапускается корректно
    imap_state.push_counter += 1
    assert wait_for(lambda: len(tg.requests) == 2, timeout=5)
    assert repository.list_accounts()[0]["last_status"] == "ok"


def test_idle_stops_quickly_on_shutdown(env, monkeypatch):
    imap_state, tg, d = env
    imap_state.idle = True
    monkeypatch.setattr(daemon, "IDLE_CYCLE", 60)
    service.add_account("gmail", "me@example.com", "secret", verify=False)
    d.start()
    assert wait_for(lambda: len(repository.list_messages()) == 3)
    time.sleep(0.5)
    started = time.time()
    d.stop()
    assert time.time() - started < 5, "остановка не должна ждать конца IDLE-цикла"
    assert not any(w.is_alive() for w in d.workers.values())
