from __future__ import annotations

from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.bot_gateway import BotDeliveryStatus, BotGatewayService, InMemoryBotTransport
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor
from agentbridge.persistence import SQLAlchemyRepository


def create_session_with_turn(control: ControlPlane, tmp_path):
    commands = CommandService(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-bot",
    )
    commands.execute(
        commands.parse(
            raw_text=f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="bot-project",
            trace_id="bot-project",
        )
    )
    session_result = commands.execute(
        commands.parse(
            raw_text="/agent session new Bot Delivery",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="bot-session",
            trace_id="bot-session",
        )
    )
    commands.execute(
        commands.parse(
            raw_text="/agent ask run renderer delivery",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="bot-turn",
            trace_id="bot-turn",
        )
    )
    return context, session_result.data["session_id"]


def test_bot_gateway_delivers_rendered_events_idempotently(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)

    first = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )
    second = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )

    assert [record.status for record in first] == [
        BotDeliveryStatus.SENT,
        BotDeliveryStatus.SENT,
    ]
    assert [record.status for record in second] == [
        BotDeliveryStatus.SKIPPED_DUPLICATE,
        BotDeliveryStatus.SKIPPED_DUPLICATE,
    ]
    assert len(transport.sent) == 2
    assert "Bot Delivery" in transport.sent[0]["text"]
    assert "任务已排队" in transport.sent[1]["text"]


def test_bot_gateway_can_resume_delivery_after_seq(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)

    records = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )

    assert len(records) == 1
    assert records[0].event_seq == 2
    assert "任务已排队" in records[0].text


def test_bot_gateway_delivery_records_survive_repository_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'bot-delivery.db'}"
    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    context, session_id = create_session_with_turn(first_control, tmp_path)
    first_transport = InMemoryBotTransport()
    first_gateway = BotGatewayService(first_control, transport=first_transport)

    first_records = first_gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    second_transport = InMemoryBotTransport()
    second_gateway = BotGatewayService(second_control, transport=second_transport)
    replay_records = second_gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )

    assert len(first_records) == 2
    assert len(first_transport.sent) == 2
    assert [record.status for record in replay_records] == [
        BotDeliveryStatus.SKIPPED_DUPLICATE,
        BotDeliveryStatus.SKIPPED_DUPLICATE,
    ]
    assert second_transport.sent == []


def test_bot_gateway_api_delivers_and_lists_records(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-bot-api",
    }
    project_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "bot-api-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Bot API",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "bot-api-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]
    context_id = project_response.json()["data"]["project"]["id"]
    # API command execution creates a chat context from the chat identity; fetch it via context API.
    context_response = client.post("/api/v1/chat-contexts", json=chat)
    chat_context_id = context_response.json()["id"]

    delivery_response = client.post(
        "/api/v1/bot-gateway/deliver-session-events",
        json={"session_id": session_id, "chat_context_id": chat_context_id},
    )
    duplicate_response = client.post(
        "/api/v1/bot-gateway/deliver-session-events",
        json={"session_id": session_id, "chat_context_id": chat_context_id},
    )
    records_response = client.get(
        "/api/v1/bot-gateway/deliveries",
        params={"chat_context_id": chat_context_id},
    )

    assert context_id
    assert delivery_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert records_response.status_code == 200
    assert [record["status"] for record in duplicate_response.json()] == [
        "skipped_duplicate"
    ]
    assert len(records_response.json()) == 1
