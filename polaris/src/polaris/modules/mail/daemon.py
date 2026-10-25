"""Фоновый демон почты: по потоку на ящик + отправщик очереди в Telegram.

Запускается в процессе бота. Настройки (ящики и получатели) читаются из БД
каждые несколько секунд, поэтому изменения из Mini App подхватываются сами.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from polaris.modules.mail import imap, repository, telegram, tgformat
from polaris.modules.mail.destinations import Destination

logger = logging.getLogger(__name__)

POLL_INTERVAL = max(15, int(os.getenv("MAIL_POLL_INTERVAL", "30")))   # опрос, если IDLE недоступен
USE_IDLE = os.getenv("MAIL_USE_IDLE", "1").strip().lower() not in ("0", "false", "no", "off")
IDLE_CYCLE = 240                 # IDLE перезапускается раз в 4 минуты: держит соединение живым через NAT
RECONCILE_INTERVAL = 10          # как часто сверять список ящиков с БД
SENDER_INTERVAL = 3              # как часто смотреть очередь доставки
SEND_GAP = 1.1                   # пауза между сообщениями (лимит Telegram ≈ 1/с на чат)
RECONNECT_START, RECONNECT_MAX = 5, 300
INITIAL_IMPORT = 20              # сколько последних писем показать в Mini App при первом подключении
KEEP_MESSAGES = 200              # сколько писем хранить на ящик
RETRY_DELAYS = (30, 120, 600, 3600, 21600)   # паузы между попытками доставки
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
MAX_ATTEMPTS_CONFIG_ERROR = 3    # 400/403 — скорее всего неверная настройка, долго не мучаем


def _safe_text(error: Exception, secrets: tuple[str, ...]) -> str:
    text = str(error)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[скрыто]")
    return text[:300]


class AccountWorker(threading.Thread):
    """Один IMAP-ящик: подключение, поиск новых писем по UID, переподключение."""

    def __init__(self, account: dict[str, Any]) -> None:
        super().__init__(name=f"mail-{account['id']}", daemon=True)
        self.account = account
        self.account_id = int(account["id"])
        self.fingerprint = fingerprint(account)
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    # ── цикл ──
    def run(self) -> None:
        delay = RECONNECT_START
        logger.info("Mail: запуск ящика %s", self.account["email"])
        while not self.stop_event.is_set():
            conn = None
            try:
                conn = imap.connect(self.account["provider"], self.account["email"], self.account["password"])
                validity = imap.uidvalidity(conn)
                self._sync(conn, validity)
                repository.set_account_status(self.account_id, "ok")
                delay = RECONNECT_START
                use_idle = USE_IDLE and imap.supports_idle(conn)
                logger.info("Mail: %s — режим %s", self.account["email"],
                            "IDLE (мгновенно)" if use_idle else f"опрос раз в {POLL_INTERVAL} с")
                while not self.stop_event.is_set():
                    if use_idle:
                        try:
                            imap.idle_wait(conn, IDLE_CYCLE, self.stop_event)
                        except imap.MailConnectionError as exc:
                            if "IDLE" not in str(exc):
                                raise
                            logger.warning("Mail: %s — IDLE не работает (%s), перехожу на опрос", self.account["email"], exc)
                            use_idle = False
                            continue
                    else:
                        if self.stop_event.wait(POLL_INTERVAL):
                            break
                        imap.noop(conn)
                    if self.stop_event.is_set():
                        break
                    self._sync(conn, validity)
                    repository.set_account_status(self.account_id, "ok")
            except Exception as exc:  # noqa: BLE001
                if self.stop_event.is_set():
                    break
                message = _safe_text(exc, (self.account["password"],))
                logger.warning("Mail: ящик %s — ошибка, повтор через %ds: %s", self.account["email"], delay, message)
                try:
                    repository.set_account_status(self.account_id, "error", message)
                except Exception:  # noqa: BLE001
                    pass
                self.stop_event.wait(delay)
                delay = min(delay * 2, RECONNECT_MAX)
            finally:
                if conn is not None:
                    imap.logout(conn)
        logger.info("Mail: ящик %s остановлен", self.account["email"])

    # ── синхронизация ──
    def _sync(self, conn, validity: str) -> None:
        state = repository.get_state(self.account_id)
        uids = imap.all_uids(conn) if (state is None or state[0] != validity) else None

        if uids is not None:
            # Первый запуск или сменился UIDVALIDITY: показываем последние письма, но в Telegram не шлём.
            if state is not None:
                logger.warning("Mail: %s — UIDVALIDITY изменился (%s → %s)", self.account["email"], state[0], validity)
            highest = uids[-1] if uids else 0
            for uid in uids[-INITIAL_IMPORT:]:
                self._ingest(conn, validity, uid, notify=False)
            repository.save_state(self.account_id, validity, highest)
            repository.prune_messages(self.account_id, KEEP_MESSAGES)
            return

        last_uid = state[1]
        fresh = imap.new_uids(conn, last_uid)
        for uid in fresh:
            if self.stop_event.is_set():
                return
            self._ingest(conn, validity, uid, notify=True)
        if fresh:
            repository.prune_messages(self.account_id, KEEP_MESSAGES)

    def _ingest(self, conn, validity: str, uid: int, notify: bool) -> None:
        raw, truncated = imap.fetch_message_bytes(conn, uid)
        parsed = imap.parse_message(raw, truncated)
        destinations = repository.enabled_destination_ids() if notify else []
        # импорт «для просмотра» позицию не двигает: её ставит _sync после всей пачки
        repository.ingest_message(self.account_id, validity, uid, parsed, destinations, advance=notify)
        logger.info("Mail: %s — письмо UID %s сохранено%s", self.account["email"], uid,
                    f", в очереди: {len(destinations)}" if notify else "")


def fingerprint(account: dict[str, Any]) -> tuple:
    return (account["provider"], account["email"], account["password"])


class MailDaemon:
    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token
        self.stop_event = threading.Event()
        self.workers: dict[int, AccountWorker] = {}
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        repository.ensure_schema()
        for target, name in ((self._manager_loop, "mail-manager"), (self._sender_loop, "mail-sender")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("Mail: демон запущен (опрос ящиков каждые %d с)", POLL_INTERVAL)

    def stop(self) -> None:
        self.stop_event.set()
        for worker in list(self.workers.values()):
            worker.stop()
        for thread in self._threads:
            thread.join(timeout=5)
        for worker in list(self.workers.values()):
            worker.join(timeout=5)

    # ── менеджер: сверяет потоки с настройками ──
    def _manager_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.reconcile()
            except Exception:  # noqa: BLE001
                logger.exception("Mail: ошибка сверки ящиков")
            self.stop_event.wait(RECONCILE_INTERVAL)

    def reconcile(self) -> None:
        desired = {a["id"]: a for a in repository.list_accounts(include_secret=True) if a["enabled"]}
        for account_id, worker in list(self.workers.items()):
            account = desired.get(account_id)
            if account is None or worker.fingerprint != fingerprint(account) or not worker.is_alive():
                worker.stop()
                del self.workers[account_id]
        for account_id, account in desired.items():
            if account_id not in self.workers:
                worker = AccountWorker(account)
                self.workers[account_id] = worker
                worker.start()

    # ── отправщик очереди в Telegram ──
    def _sender_loop(self) -> None:
        if not self.bot_token:
            logger.warning("Mail: TELEGRAM_BOT_TOKEN не задан — письма сохраняются, но в Telegram не уходят")
        while not self.stop_event.is_set():
            try:
                sent_any = self.deliver_due()
            except Exception:  # noqa: BLE001
                logger.exception("Mail: ошибка отправки очереди")
                sent_any = False
            self.stop_event.wait(SEND_GAP if sent_any else SENDER_INTERVAL)

    def deliver_due(self) -> bool:
        if not self.bot_token:
            return False
        rows = repository.due_deliveries(limit=10)
        for row in rows:
            if self.stop_event.is_set():
                break
            self._deliver_one(row)
            self.stop_event.wait(SEND_GAP)
        return bool(rows)

    def _deliver_one(self, row: dict[str, Any]) -> None:
        dest = Destination(row["chat_id"], row["thread_id"], row["kind"])
        account_name = row["account_label"] or row["account_email"]
        args = (account_name, row["subject"], row["sender"], row["recipient"], row["date_text"],
                row["body"], row["attachments"])
        try:
            telegram.send_rich(
                self.bot_token, dest,
                tgformat.build_rich_markdown(*args),
                fallback_html=tgformat.build_fallback_html(*args),
            )
        except telegram.TelegramError as exc:
            self._on_failure(row, exc)
            return
        repository.mark_delivery_sent(row["delivery_id"])
        repository.set_destination_status(row["destination_id"], "ok")

    def _on_failure(self, row: dict[str, Any], exc: telegram.TelegramError) -> None:
        message = telegram.explain(exc)
        delivery_id, destination_id = row["delivery_id"], row["destination_id"]

        if exc.status == 429:  # лимит Telegram — это не ошибка получателя, просто ждём
            repository.mark_delivery_retry(delivery_id, row["attempts"], max(exc.retry_after, 1) + 1, message)
            return

        attempts = row["attempts"] + 1
        limit = MAX_ATTEMPTS_CONFIG_ERROR if exc.permanent else MAX_ATTEMPTS
        repository.set_destination_status(destination_id, "error", message)
        if attempts >= limit:
            logger.error("Mail: доставка %s не удалась окончательно: %s", delivery_id, message)
            repository.mark_delivery_failed(delivery_id, attempts, message)
        else:
            logger.warning("Mail: доставка %s не удалась (попытка %d): %s", delivery_id, attempts, message)
            repository.mark_delivery_retry(delivery_id, attempts, RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)], message)
