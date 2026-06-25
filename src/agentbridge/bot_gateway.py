from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from agentbridge.control_plane import ControlPlane
from agentbridge.renderer import OneBotV11TextRenderer, RenderDocument, document_from_event


class BotPlatform(StrEnum):
    ONEBOT_V11 = "onebot.v11"
    PLAIN_TEXT = "plain_text"


class BotDeliveryStatus(StrEnum):
    SENT = "sent"
    SKIPPED_DUPLICATE = "skipped_duplicate"


class BotDeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    idempotency_key: str
    platform: BotPlatform
    chat_context_id: str
    event_id: str
    event_seq: int
    message_index: int
    platform_message_id: str | None = None
    text: str
    status: BotDeliveryStatus


class BotTransport(Protocol):
    def send_text(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
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
        text: str,
        idempotency_key: str,
    ) -> str:
        message_id = f"msg_{uuid4().hex[:12]}"
        self.sent.append(
            {
                "platform": platform.value,
                "chat_context_id": chat_context_id,
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
    ) -> None:
        self.control = control
        self.transport = transport or InMemoryBotTransport()
        self.renderer = renderer or OneBotV11TextRenderer()
        self.delivery_records: dict[str, BotDeliveryRecord] = {}

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
        records: list[BotDeliveryRecord] = []
        for index, text in enumerate(messages):
            idempotency_key = f"{platform.value}:{chat_context_id}:{event_id}:{index}"
            existing = self.delivery_records.get(idempotency_key)
            if existing:
                duplicate = existing.model_copy(
                    update={"status": BotDeliveryStatus.SKIPPED_DUPLICATE}
                )
                records.append(duplicate)
                continue
            platform_message_id = self.transport.send_text(
                platform=platform,
                chat_context_id=chat_context_id,
                text=text,
                idempotency_key=idempotency_key,
            )
            record = BotDeliveryRecord(
                id=f"bdlv_{uuid4().hex[:12]}",
                idempotency_key=idempotency_key,
                platform=platform,
                chat_context_id=chat_context_id,
                event_id=event_id,
                event_seq=event_seq,
                message_index=index,
                platform_message_id=platform_message_id,
                text=text,
                status=BotDeliveryStatus.SENT,
            )
            self.delivery_records[idempotency_key] = record
            records.append(record)
        return records

    def list_records(self, chat_context_id: str | None = None) -> list[BotDeliveryRecord]:
        records = list(self.delivery_records.values())
        if chat_context_id:
            records = [record for record in records if record.chat_context_id == chat_context_id]
        return records
