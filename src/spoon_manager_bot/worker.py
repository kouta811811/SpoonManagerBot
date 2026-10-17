"""配信中だけ動くWorker（ECS Fargate Task、設計§4・§5）。

スレッドは2本:
- ストリーム用スレッド: `GET /v1/live/events` を読み、イベントをキューへ入れる
- メインスレッド: キューのイベントを処理し、1秒ごとにお礼の枠とチャット送信、
  30秒ごとにleaseの延長とscopeの確認をする

終了コード: 0 = 正常（配信終了・待機上限・停止要求など） / 2 = 人の対応が必要 / 1 = 設定・起動エラー
"""

from __future__ import annotations

import logging
import queue
import secrets
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType

from spoon_manager_bot import aws
from spoon_manager_bot.bot.router import EventRouter
from spoon_manager_bot.chat.outbox import Outbox
from spoon_manager_bot.config import ConfigError, Settings, load_settings
from spoon_manager_bot.domain.events import SpoonEvent
from spoon_manager_bot.domain.thanks import TemplateSet, ThanksKind
from spoon_manager_bot.observability.logger import log_event
from spoon_manager_bot.repositories.state import LeaseRepository, StateError
from spoon_manager_bot.spoon.chat_client import ChatOutcome, ChatSender, SpoonChatClient
from spoon_manager_bot.spoon.oauth import SpoonOAuthClient
from spoon_manager_bot.spoon.sse_client import HttpStreamConnector, StreamConnector
from spoon_manager_bot.stream import StopReason, StreamSupervisor
from spoon_manager_bot.thanks.commands import ThanksCommandHandler
from spoon_manager_bot.thanks.repository import DynamoDbThanksRepository, ThanksRepository
from spoon_manager_bot.thanks.service import ThanksService
from spoon_manager_bot.tokens import ReauthRequired, SsmTokenStore, TokenManager, TokenStoreError

EVENT_SCOPES = frozenset({"events.chat", "events.presence", "events.like", "events.donation"})
LEASE_DURATION = 120.0
HEARTBEAT_INTERVAL = 30.0
TICK = 1.0
DRAIN_SECONDS = 5.0

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEEDS_OPERATOR = 2


@dataclass(slots=True)
class WorkerDeps:
    tokens: TokenManager
    connector: StreamConnector
    sender: ChatSender
    thanks_repository: ThanksRepository
    lease: LeaseRepository | None
    lease_token: str | None
    ignored_user_ids: frozenset[str]
    live_wait_limit: float


class Worker:
    def __init__(
        self,
        deps: WorkerDeps,
        *,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
    ) -> None:
        self._deps = deps
        self._clock = clock
        self._heartbeat_interval = heartbeat_interval
        self._events: queue.Queue[SpoonEvent] = queue.Queue(maxsize=10_000)
        self._shutdown = threading.Event()
        self.supervisor = StreamSupervisor(
            deps.connector,
            deps.tokens,
            self._enqueue,
            live_wait_limit=deps.live_wait_limit,
        )

    def request_shutdown(self) -> None:
        self._shutdown.set()
        self.supervisor.stop()

    def run(self) -> tuple[StopReason, int]:
        deps = self._deps
        try:
            deps.tokens.load()
        except ReauthRequired:
            log_event("worker_stopped", result=StopReason.REAUTH_REQUIRED.value, level=40)
            return StopReason.REAUTH_REQUIRED, EXIT_NEEDS_OPERATOR
        scopes = deps.tokens.scopes
        if not scopes & EVENT_SCOPES:
            log_event("worker_stopped", result=StopReason.CONSENT_REQUIRED.value)
            return StopReason.CONSENT_REQUIRED, EXIT_NEEDS_OPERATOR

        templates = TemplateSet(deps.thanks_repository.load_all())
        router = EventRouter(
            thanks=ThanksService(templates),
            thanks_commands=ThanksCommandHandler(templates, deps.thanks_repository),
            outbox=Outbox(),
            scopes=scopes,
            ignored_user_ids=deps.ignored_user_ids,
        )
        log_event("worker_started", pending=sum(len(templates.tiers(k)) for k in ThanksKind))

        result: dict[str, StopReason] = {}

        def stream() -> None:
            result["reason"] = self.supervisor.run()

        thread = threading.Thread(target=stream, name="sse", daemon=True)
        thread.start()

        next_heartbeat = self._clock() + self._heartbeat_interval
        lease_lost = False
        while thread.is_alive() or not self._events.empty():
            self._process(router, timeout=TICK)
            now = self._clock()
            if now >= next_heartbeat:
                next_heartbeat = now + self._heartbeat_interval
                router.update_scopes(deps.tokens.scopes)
                if not self._heartbeat():
                    lease_lost = True
                    self.request_shutdown()

        # 最後に溜まっている返信を少しだけ送る
        deadline = self._clock() + DRAIN_SECONDS
        while self._clock() < deadline and router.flush(deps.sender, self._clock()) is not None:
            pass

        reason = StopReason.LEASE_LOST if lease_lost else result.get("reason", StopReason.SHUTDOWN)
        self._release()
        code = EXIT_NEEDS_OPERATOR if reason.needs_operator else EXIT_OK
        log_event("worker_stopped", result=reason.value, reconnect_count=self.supervisor.reconnects)
        return reason, code

    def _enqueue(self, event: SpoonEvent) -> None:
        try:
            self._events.put(event, timeout=1.0)
        except queue.Full:
            log_event("event_dropped", result="queue_full")

    def _process(self, router: EventRouter, *, timeout: float) -> None:
        try:
            event: SpoonEvent | None = self._events.get(timeout=timeout)
        except queue.Empty:
            event = None
        now = self._clock()
        if event is not None:
            router.handle(event, now)
        else:
            router.tick(now)
        delivery = router.flush(self._deps.sender, now)
        if delivery is not None and delivery.result.outcome is ChatOutcome.UNAUTHORIZED:
            try:
                self._deps.tokens.refresh()
            except ReauthRequired:
                log_event("chat_reauth_required", result="stop")
                self.request_shutdown()
            except Exception:
                log_event("chat_token_refresh_failed", result="retry_later")

    def _heartbeat(self) -> bool:
        lease, token = self._deps.lease, self._deps.lease_token
        if lease is None or token is None:
            return True
        try:
            return lease.heartbeat(token, LEASE_DURATION)
        except StateError:
            log_event("lease_heartbeat_failed", result="retry_later")
            return True

    def _release(self) -> None:
        lease, token = self._deps.lease, self._deps.lease_token
        if lease is None or token is None:
            return
        try:
            lease.release(token)
        except StateError:
            log_event("lease_release_failed", result="expires_later")


def build_worker(settings: Settings) -> Worker:
    dynamodb = aws.client("dynamodb", settings.aws_region)
    ssm = aws.client("ssm", settings.aws_region)
    store = SsmTokenStore(ssm, settings.ssm_prefix)
    oauth = SpoonOAuthClient(settings.spoon_region.api_base_url, store.load_credentials())
    tokens = TokenManager(store, oauth)
    lease = LeaseRepository(dynamodb, settings.state_table)
    lease_token = settings.lease_token
    if lease_token is None:
        # Control Lambdaを通さずに起動した場合は自分でleaseを取る
        lease_token = secrets.token_urlsafe(16)
        if not lease.acquire(lease_token, LEASE_DURATION):
            raise ConfigError("another worker holds the lease")
    elif not lease.heartbeat(lease_token, LEASE_DURATION):
        raise ConfigError("lease token does not match the current lease")
    deps = WorkerDeps(
        tokens=tokens,
        connector=HttpStreamConnector(settings.spoon_region.api_base_url),
        sender=SpoonChatClient(settings.spoon_region.api_base_url, tokens.access_token),
        thanks_repository=DynamoDbThanksRepository(dynamodb, settings.state_table),
        lease=lease,
        lease_token=lease_token,
        ignored_user_ids=settings.ignored_user_ids,
        live_wait_limit=settings.live_wait_limit,
    )
    return Worker(deps)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    try:
        worker = build_worker(load_settings())
    except (ConfigError, TokenStoreError, StateError) as exc:
        log_event("worker_config_error", error_code=type(exc).__name__, level=40)
        return EXIT_ERROR

    def on_signal(signum: int, frame: FrameType | None) -> None:
        log_event("worker_signal", status=signum)
        worker.request_shutdown()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    _, code = worker.run()
    return code


if __name__ == "__main__":
    sys.exit(main())
