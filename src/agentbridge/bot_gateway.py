from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import BotDeliveryRecord, BotDeliveryStatus, BotPlatform, ChatContext
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
    ) -> None:
        self.control = control
        self.transport = transport or InMemoryBotTransport()
        self.renderer = renderer or OneBotV11TextRenderer()

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
                duplicate = existing.model_copy(
                    update={"status": BotDeliveryStatus.SKIPPED_DUPLICATE}
                )
                records.append(duplicate)
                continue
            platform_message_id = self.transport.send_text(
                platform=platform,
                chat_context_id=chat_context_id,
                chat_context=chat_context,
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
            self.control.repository.store_bot_delivery_record(record)
            records.append(record)
        return records

    def list_records(self, chat_context_id: str | None = None) -> list[BotDeliveryRecord]:
        return self.control.repository.list_bot_delivery_records(chat_context_id)
