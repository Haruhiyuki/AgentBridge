from __future__ import annotations

from datetime import datetime, timedelta
from threading import Event, RLock, Thread, current_thread
from typing import Protocol
from uuid import uuid4

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    AgentBridgeError,
    BotDeliveryRecord,
    BotDeliveryStatus,
    BotPlatform,
    ChatContext,
    utc_now,
)
from agentbridge.renderer import OneBotV11TextRenderer, RenderDocument, document_from_event


class BotTransport(Protocol):
    def send_text(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
        chat_context: ChatContext,
        text: str,
        idempotency_key: str,
    ) -> str: ...


class InMemoryBotTransport:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send_text(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
        chat_context: ChatContext,
        text: str,
        idempotency_key: str,
    ) -> str:
        message_id = f"msg_{uuid4().hex[:12]}"
        self.sent.append(
            {
                "platform": platform.value,
                "chat_context_id": chat_context_id,
                "chat_space_id": chat_context.chat_space_id,
                "text": text,
                "idempotency_key": idempotency_key,
                "platform_message_id": message_id,
            }
        )
        return message_id


class BotGatewayService:
    def __init__(
        self,
        control: ControlPlane,
        transport: BotTransport | None = None,
        renderer: OneBotV11TextRenderer | None = None,
        retry_base_seconds: int = 30,
        retry_max_seconds: int = 300,
    ) -> None:
        self.control = control
        self.transport = transport or InMemoryBotTransport()
        self.renderer = renderer or OneBotV11TextRenderer()
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds

    def deliver_session_events(
        self,
        *,
        session_id: str,
        chat_context_id: str,
        platform: BotPlatform = BotPlatform.ONEBOT_V11,
        after_seq: int | None = None,
        limit: int = 100,
    ) -> list[BotDeliveryRecord]:
        self.control.repository.get_session(session_id)
        self.control.repository.get_chat_context(chat_context_id)
        events = self.control.repository.list_events(
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
        )
        records: list[BotDeliveryRecord] = []
        for event in events:
            document = document_from_event(event)
            records.extend(
                self.deliver_document(
                    document=document,
                    event_id=event.id,
                    event_seq=event.seq,
                    chat_context_id=chat_context_id,
                    platform=platform,
                )
            )
        return records

    def deliver_document(
        self,
        *,
        document: RenderDocument,
        event_id: str,
        event_seq: int,
        chat_context_id: str,
        platform: BotPlatform,
    ) -> list[BotDeliveryRecord]:
        messages = self.renderer.render(document)
        chat_context = self.control.repository.get_chat_context(chat_context_id)
        records: list[BotDeliveryRecord] = []
        for index, text in enumerate(messages):
            idempotency_key = f"{platform.value}:{chat_context_id}:{event_id}:{index}"
            existing = self.control.repository.get_bot_delivery_record(idempotency_key)
            if existing:
                if existing.status == BotDeliveryStatus.FAILED:
                    records.append(existing)
                    continue
                duplicate = existing.model_copy(
                    update={"status": BotDeliveryStatus.SKIPPED_DUPLICATE}
                )
                records.append(duplicate)
                continue
            records.append(
                self._send_and_store(
                    idempotency_key=idempotency_key,
                    platform=platform,
                    chat_context=chat_context,
                    event_id=event_id,
                    event_seq=event_seq,
                    message_index=index,
                    text=text,
                    existing=None,
                )
            )
        return records

    def list_records(
        self,
        chat_context_id: str | None = None,
        status: BotDeliveryStatus | None = None,
    ) -> list[BotDeliveryRecord]:
        return self.control.repository.list_bot_delivery_records(chat_context_id, status=status)

    def retry_failed_deliveries(
        self,
        *,
        chat_context_id: str | None = None,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[BotDeliveryRecord]:
        now = now or utc_now()
        candidates = self.control.repository.list_bot_delivery_records(
            chat_context_id=chat_context_id,
            status=BotDeliveryStatus.FAILED,
        )
        retried: list[BotDeliveryRecord] = []
        for record in candidates:
            if len(retried) >= limit:
                break
            if record.next_retry_at and record.next_retry_at > now:
                continue
            chat_context = self.control.repository.get_chat_context(record.chat_context_id)
            retried.append(
                self._send_and_store(
                    idempotency_key=record.idempotency_key,
                    platform=record.platform,
                    chat_context=chat_context,
                    event_id=record.event_id,
                    event_seq=record.event_seq,
                    message_index=record.message_index,
                    text=record.text,
                    existing=record,
                )
            )
        return retried

    def _send_and_store(
        self,
        *,
        idempotency_key: str,
        platform: BotPlatform,
        chat_context: ChatContext,
        event_id: str,
        event_seq: int,
        message_index: int,
        text: str,
        existing: BotDeliveryRecord | None,
    ) -> BotDeliveryRecord:
        now = utc_now()
        attempt_count = (existing.attempt_count + 1) if existing else 1
        try:
            platform_message_id = self.transport.send_text(
                platform=platform,
                chat_context_id=chat_context.id,
                chat_context=chat_context,
                text=text,
                idempotency_key=idempotency_key,
            )
            record = BotDeliveryRecord(
                id=existing.id if existing else f"bdlv_{uuid4().hex[:12]}",
                idempotency_key=idempotency_key,
                platform=platform,
                chat_context_id=chat_context.id,
                event_id=event_id,
                event_seq=event_seq,
                message_index=message_index,
                platform_message_id=platform_message_id,
                text=text,
                status=BotDeliveryStatus.SENT,
                attempt_count=attempt_count,
                last_error=None,
                next_retry_at=None,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
        except AgentBridgeError as exc:
            record = self._failed_record(
                idempotency_key=idempotency_key,
                platform=platform,
                chat_context=chat_context,
                event_id=event_id,
                event_seq=event_seq,
                message_index=message_index,
                text=text,
                attempt_count=attempt_count,
                error=exc.message,
                existing=existing,
                now=now,
            )
        except Exception as exc:
            record = self._failed_record(
                idempotency_key=idempotency_key,
                platform=platform,
                chat_context=chat_context,
                event_id=event_id,
                event_seq=event_seq,
                message_index=message_index,
                text=text,
                attempt_count=attempt_count,
                error=str(exc),
                existing=existing,
                now=now,
            )
        self.control.repository.store_bot_delivery_record(record)
        return record

    def _failed_record(
        self,
        *,
        idempotency_key: str,
        platform: BotPlatform,
        chat_context: ChatContext,
        event_id: str,
        event_seq: int,
        message_index: int,
        text: str,
        attempt_count: int,
        error: str,
        existing: BotDeliveryRecord | None,
        now: datetime,
    ) -> BotDeliveryRecord:
        return BotDeliveryRecord(
            id=existing.id if existing else f"bdlv_{uuid4().hex[:12]}",
            idempotency_key=idempotency_key,
            platform=platform,
            chat_context_id=chat_context.id,
            event_id=event_id,
            event_seq=event_seq,
            message_index=message_index,
            platform_message_id=existing.platform_message_id if existing else None,
            text=text,
            status=BotDeliveryStatus.FAILED,
            attempt_count=attempt_count,
            last_error=error,
            next_retry_at=now + timedelta(seconds=self._retry_delay(attempt_count)),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    def _retry_delay(self, attempt_count: int) -> int:
        delay = self.retry_base_seconds * (2 ** max(attempt_count - 1, 0))
        return min(delay, self.retry_max_seconds)


class BotDeliveryRetryWorker:
    """Background scheduler for due Bot delivery retries."""

    def __init__(
        self,
        gateway: BotGatewayService,
        *,
        enabled: bool = False,
        interval_seconds: float = 30.0,
        batch_size: int = 100,
        chat_context_id: str | None = None,
    ) -> None:
        self.gateway = gateway
        self.enabled = enabled
        self.interval_seconds = max(float(interval_seconds), 0.1)
        self.batch_size = max(int(batch_size), 1)
        self.chat_context_id = chat_context_id
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.started_at: datetime | None = None
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.last_record_count = 0
        self.run_count = 0

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self.started_at = utc_now()
            self._thread = Thread(
                target=self._run_loop,
                name="agentbridge-bot-retry-worker",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        with self._lock:
            thread = self._thread
            if thread is None:
                return False
            self._stop_event.set()
        if thread is not current_thread():
            thread.join(timeout=timeout)
        with self._lock:
            stopped = not thread.is_alive()
            if stopped:
                self._thread = None
            return stopped

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def run_once(
        self,
        *,
        chat_context_id: str | None = None,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> list[BotDeliveryRecord]:
        retry_limit = max(int(limit or self.batch_size), 1)
        retry_context = chat_context_id if chat_context_id is not None else self.chat_context_id
        with self._lock:
            self.run_count += 1
            self.last_run_at = utc_now()
        try:
            records = self.gateway.retry_failed_deliveries(
                chat_context_id=retry_context,
                now=now,
                limit=retry_limit,
            )
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)
                self.last_record_count = 0
            return []
        with self._lock:
            self.last_error = None
            self.last_record_count = len(records)
        return records

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "batch_size": self.batch_size,
                "chat_context_id": self.chat_context_id,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
                "last_error": self.last_error,
                "last_record_count": self.last_record_count,
                "run_count": self.run_count,
            }

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.interval_seconds)
