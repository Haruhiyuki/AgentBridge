from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.bot_gateway import (
    BotDeliveryRateLimiter,
    BotDeliveryRetryWorker,
    BotDeliveryStatus,
    BotGatewayService,
    BotRateLimitPolicy,
    InMemoryBotTransport,
)
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.device_certificate_scan import DeviceCertificateScanWorker
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    BotDeliveryResultAction,
    BotPlatform,
    ErrorCode,
    InteractionType,
    SemanticEventSource,
)
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


class FlakyTransport(InMemoryBotTransport):
    def __init__(self, fail_times: int) -> None:
        super().__init__()
        self.fail_times = fail_times

    def send_text(self, **kwargs):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "temporary platform failure",
                status_code=502,
            )
        return super().send_text(**kwargs)


class RateLimitedTransport(InMemoryBotTransport):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds
        self.calls = 0

    def send_text(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise AgentBridgeError(
                ErrorCode.QUOTA_EXCEEDED,
                "platform rate limited",
                status_code=429,
                details={"retry_after_seconds": self.retry_after_seconds},
            )
        return super().send_text(**kwargs)


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


def test_bot_gateway_delivers_filtered_semantic_events_idempotently():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-security",
    )
    control.emit_event(
        event_type="device_identity.certificates_scanned",
        source=SemanticEventSource.CONTROL_PLANE,
        trace_id="security-scan-alert",
        payload={
            "total_device_count": 1,
            "action_required_count": 1,
            "status_counts": {"expired": 1},
            "warning_days": 7,
            "scanned_at": "2026-06-26T00:00:00Z",
            "action_required_devices": [
                {
                    "device_id": "expired-device",
                    "certificate_health_status": "expired",
                    "expired_count": 1,
                }
            ],
        },
    )
    control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="unrelated",
        payload={"text": "ignore me"},
    )
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)

    first = gateway.deliver_events(
        chat_context_id=context.id,
        payload_field="status_counts.expired",
        payload_value="1",
    )
    second = gateway.deliver_events(
        chat_context_id=context.id,
        payload_field="status_counts.expired",
        payload_value="1",
    )

    assert [record.status for record in first] == [BotDeliveryStatus.SENT]
    assert [record.status for record in second] == [BotDeliveryStatus.SKIPPED_DUPLICATE]
    assert len(transport.sent) == 1
    assert "设备证书扫描" in transport.sent[0]["text"]
    assert "expired-device" in transport.sent[0]["text"]


def test_bot_gateway_rejects_unfiltered_cross_stream_delivery():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-security",
    )
    gateway = BotGatewayService(control, transport=InMemoryBotTransport())

    try:
        gateway.deliver_events(chat_context_id=context.id)
    except AgentBridgeError as exc:
        error = exc
    else:
        raise AssertionError("deliver_events should require at least one event filter")

    assert error.status_code == 400
    assert error.code == ErrorCode.COMMAND_ARGUMENT_INVALID


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


def test_bot_gateway_records_failures_and_retries_due_deliveries(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = FlakyTransport(fail_times=1)
    gateway = BotGatewayService(control, transport=transport, retry_base_seconds=0)

    first = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )
    retry = gateway.retry_failed_deliveries(chat_context_id=context.id)

    assert len(first) == 1
    assert first[0].status == BotDeliveryStatus.FAILED
    assert first[0].attempt_count == 1
    assert first[0].last_error == "temporary platform failure"
    assert retry[0].status == BotDeliveryStatus.SENT
    assert retry[0].attempt_count == 2
    assert retry[0].last_error is None
    assert len(transport.sent) == 1


def test_bot_gateway_rate_limit_schedules_unsent_delivery_for_retry(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = InMemoryBotTransport()
    limiter = BotDeliveryRateLimiter(
        [
            BotRateLimitPolicy(
                platform=BotPlatform.ONEBOT_V11,
                capacity=1,
                window_seconds=60,
            )
        ]
    )
    gateway = BotGatewayService(control, transport=transport, rate_limiter=limiter)

    records = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )
    scheduled = records[1]

    assert [record.status for record in records] == [
        BotDeliveryStatus.SENT,
        BotDeliveryStatus.RETRYING,
    ]
    assert scheduled.attempt_count == 0
    assert scheduled.last_error == "rate limited"
    assert scheduled.next_retry_at is not None
    assert len(transport.sent) == 1

    retry = gateway.retry_failed_deliveries(
        chat_context_id=context.id,
        now=scheduled.next_retry_at + timedelta(seconds=1),
    )

    assert retry[0].status == BotDeliveryStatus.SENT
    assert retry[0].attempt_count == 1
    assert len(transport.sent) == 2


def test_bot_gateway_uses_platform_retry_after_for_adaptive_scheduling(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = RateLimitedTransport(retry_after_seconds=12)
    gateway = BotGatewayService(control, transport=transport)

    records = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )
    scheduled = records[0]

    assert scheduled.status == BotDeliveryStatus.RETRYING
    assert scheduled.attempt_count == 1
    assert scheduled.last_error == "platform rate limited"
    assert scheduled.next_retry_at is not None
    assert (scheduled.next_retry_at - scheduled.created_at).total_seconds() == 12
    assert transport.sent == []

    retry = gateway.retry_failed_deliveries(
        chat_context_id=context.id,
        now=scheduled.next_retry_at + timedelta(seconds=1),
    )

    assert retry[0].status == BotDeliveryStatus.SENT
    assert retry[0].attempt_count == 2
    assert len(transport.sent) == 1


def test_bot_retry_worker_retries_due_failures_with_batch_limit(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = FlakyTransport(fail_times=2)
    gateway = BotGatewayService(control, transport=transport, retry_base_seconds=0)

    failed = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )
    worker = BotDeliveryRetryWorker(gateway, batch_size=1, interval_seconds=60)
    first_retry = worker.run_once()
    second_retry = worker.run_once()

    assert [record.status for record in failed] == [
        BotDeliveryStatus.FAILED,
        BotDeliveryStatus.FAILED,
    ]
    assert len(first_retry) == 1
    assert first_retry[0].status == BotDeliveryStatus.SENT
    assert first_retry[0].attempt_count == 2
    assert len(second_retry) == 1
    assert second_retry[0].status == BotDeliveryStatus.SENT
    assert gateway.list_records(context.id, status=BotDeliveryStatus.FAILED) == []
    assert len(transport.sent) == 2
    assert worker.status()["run_count"] == 2
    assert worker.status()["last_record_count"] == 1


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


def test_failed_delivery_can_be_retried_after_repository_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'bot-retry.db'}"
    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    context, session_id = create_session_with_turn(first_control, tmp_path)
    first_gateway = BotGatewayService(
        first_control,
        transport=FlakyTransport(fail_times=1),
        retry_base_seconds=0,
    )

    failed = first_gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    second_transport = InMemoryBotTransport()
    second_gateway = BotGatewayService(second_control, transport=second_transport)
    retried = second_gateway.retry_failed_deliveries(chat_context_id=context.id)

    assert failed[0].status == BotDeliveryStatus.FAILED
    assert retried[0].status == BotDeliveryStatus.SENT
    assert retried[0].attempt_count == 2
    assert len(second_transport.sent) == 1


def test_bot_delivery_platform_state_survives_repository_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'bot-delivery-state.db'}"
    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    context, session_id = create_session_with_turn(first_control, tmp_path)
    first_gateway = BotGatewayService(first_control, transport=InMemoryBotTransport())

    delivered = first_gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )
    updated = first_gateway.record_delivery_result(
        idempotency_key=delivered[0].idempotency_key,
        action=BotDeliveryResultAction.EDIT,
        platform_message_id="msg-edited",
        text="edited platform text",
        payload={"platform_revision": 2},
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    second_gateway = BotGatewayService(second_control, transport=InMemoryBotTransport())
    records = second_gateway.list_records(context.id)

    assert updated.platform_state == "edited"
    assert records[0].platform_state == "edited"
    assert records[0].platform_message_id == "msg-edited"
    assert records[0].text == "edited platform text"
    assert records[0].edit_revision == 1
    assert records[0].platform_payload == {"platform_revision": 2}


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


def test_bot_gateway_capabilities_api_exposes_platform_contracts():
    client = TestClient(create_app())

    response = client.get("/api/v1/bot-gateway/capabilities")
    filtered = client.get(
        "/api/v1/bot-gateway/capabilities",
        params={"platform": "onebot.v11"},
    )

    assert response.status_code == 200
    capabilities = {
        item["platform"]: item for item in response.json()["capabilities"]
    }
    assert capabilities["onebot.v11"] == {
        "platform": "onebot.v11",
        "markdown": False,
        "codeBlock": True,
        "editMessage": False,
        "deleteMessage": True,
        "buttons": False,
        "selectMenu": False,
        "modalInput": False,
        "thread": False,
        "reply": True,
        "reaction": False,
        "fileUpload": False,
        "maxTextLength": 1800,
        "rateLimitProfile": "onebot.v11",
    }
    assert capabilities["plain_text"]["editMessage"] is True
    assert capabilities["plain_text"]["deleteMessage"] is True
    assert filtered.status_code == 200
    assert filtered.json()["capabilities"] == [capabilities["onebot.v11"]]


def test_bot_gateway_inbound_events_record_messages_and_execute_slash_commands():
    app = create_app()
    client = TestClient(app)

    message = client.post(
        "/api/v1/bot-gateway/inbound-events",
        json={
            "event_type": "bot.message.received",
            "bot_instance_id": "discord-main",
            "adapter": "discord",
            "platform": "discord",
            "scope": "channel",
            "channel_id": "chan-1",
            "user_id": "usr-1",
            "message_id": "msg-1",
            "text": "hello bridge",
        },
    )
    slash = client.post(
        "/api/v1/bot-gateway/inbound-events",
        json={
            "event_type": "bot.slash_command.received",
            "bot_instance_id": "discord-main",
            "adapter": "discord",
            "platform": "discord",
            "scope": "channel",
            "channel_id": "chan-1",
            "user_id": "usr-1",
            "event_id": "slash-1",
            "command": "health",
            "default_roles": ["operator"],
        },
    )

    assert message.status_code == 200
    assert message.json()["handled"] is False
    assert message.json()["event"]["type"] == "bot.message.received"
    assert message.json()["event"]["payload"]["raw_text"] == "hello bridge"
    assert message.json()["event"]["payload"]["actor_id"] == "discord:usr-1"
    assert slash.status_code == 200
    assert slash.json()["handled"] is True
    assert slash.json()["result"]["canonical_command"] == "health"
    assert slash.json()["event"]["type"] == "bot.slash_command.received"
    assert slash.json()["event"]["payload"]["raw_text"] == "health"
    assert slash.json()["event"]["payload"]["command_text"] == "/agent health"
    events = app.state.control.repository.list_semantic_events(
        event_type="bot.slash_command.received",
        trace_id=slash.json()["event"]["trace_id"],
    )
    assert len(events) == 1


def test_bot_gateway_command_registration_results_record_idempotent_events():
    app = create_app()
    client = TestClient(app)

    payload = {
        "bot_instance_id": "discord-main",
        "adapter": "discord",
        "platform": "discord",
        "scope": "guild",
        "channel_id": "guild-1",
        "registration_id": "commands-v3",
        "status": "success",
        "commands": [
            {"name": "agent", "description": "Run AgentBridge commands"},
            {"name": "agent-approve", "description": "Approve an interaction"},
        ],
        "payload": {"remote_revision": "rev-3"},
        "idempotency_key": "discord:guild-1:commands-v3",
    }

    first = client.post(
        "/api/v1/bot-gateway/command-registration-results",
        json=payload,
    )
    repeated = client.post(
        "/api/v1/bot-gateway/command-registration-results",
        json=payload,
    )

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["event"]["id"] == first.json()["event"]["id"]
    event = first.json()["event"]
    assert event["type"] == "bot.command_registration.result"
    assert event["source"] == "bot_gateway"
    assert event["idempotency_key"] == (
        "discord:guild-1:commands-v3:bot.command_registration.result"
    )
    assert event["payload"]["status"] == "succeeded"
    assert event["payload"]["command_count"] == 2
    assert event["payload"]["commands"][0]["name"] == "agent"
    assert event["payload"]["payload"] == {"remote_revision": "rev-3"}
    events = app.state.control.repository.list_semantic_events(
        event_type="bot.command_registration.result",
        trace_id="discord:guild-1:commands-v3",
    )
    assert len(events) == 1


def test_bot_gateway_command_registration_manifest_exposes_registry_specs():
    client = TestClient(create_app())

    response = client.get(
        "/api/v1/bot-gateway/command-registration-manifest",
        params={"platform": "discord"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "bot.command_registration_manifest.v1"
    assert payload["platform"] == "discord"
    assert payload["root_command"] == "agent"
    assert payload["text_prefixes"] == ["/agent", "/ab"]
    specs = {item["name"]: item for item in payload["command_specs"]}
    entries = {item["canonical_command"]: item for item in payload["native_entries"]}
    assert specs["project.create"]["required_permission"] == "project.manage"
    assert specs["project.create"]["risk"] == "medium"
    assert specs["project.create"]["requires_confirmation"] is True
    assert specs["turn.enqueue"]["argument_schema"]["required"] == ["prompt"]
    assert entries["project.create"]["name"] == "project-create"
    assert entries["approval.vote"]["required_permission"] == "approval.vote"


def test_bot_gateway_delivery_results_api_tracks_ack_edit_delete(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    client = TestClient(create_app(control))
    delivered = client.post(
        "/api/v1/bot-gateway/deliver-session-events",
        json={
            "session_id": session_id,
            "chat_context_id": context.id,
            "after_seq": 1,
        },
    )
    idempotency_key = delivered.json()[0]["idempotency_key"]

    acknowledged = client.post(
        "/api/v1/bot-gateway/delivery-results",
        json={
            "idempotency_key": idempotency_key,
            "action": "acknowledge",
            "platform_message_id": "msg-ack",
            "payload": {"receipt": "ack-1"},
        },
    )
    edited = client.post(
        "/api/v1/bot-gateway/delivery-results",
        json={
            "idempotency_key": idempotency_key,
            "action": "edit",
            "text": "edited bot message",
            "payload": {"edit_receipt": "edit-1"},
        },
    )
    deleted = client.post(
        "/api/v1/bot-gateway/delivery-results",
        json={
            "idempotency_key": idempotency_key,
            "action": "delete",
            "payload": {"delete_receipt": "delete-1"},
        },
    )
    records = client.get(
        "/api/v1/bot-gateway/deliveries",
        params={"chat_context_id": context.id},
    )

    assert acknowledged.status_code == 200
    assert acknowledged.json()["platform_state"] == "acknowledged"
    assert acknowledged.json()["acknowledged_at"] is not None
    assert acknowledged.json()["platform_message_id"] == "msg-ack"
    assert edited.status_code == 200
    assert edited.json()["platform_state"] == "edited"
    assert edited.json()["edit_revision"] == 1
    assert edited.json()["text"] == "edited bot message"
    assert deleted.status_code == 200
    assert deleted.json()["platform_state"] == "deleted"
    assert deleted.json()["deleted_at"] is not None
    assert records.json()[0]["platform_payload"] == {
        "receipt": "ack-1",
        "edit_receipt": "edit-1",
        "delete_receipt": "delete-1",
    }


def test_bot_gateway_edit_and_delete_delivery_through_transport(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)
    delivered = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )
    idempotency_key = delivered[0].idempotency_key

    edited = gateway.edit_delivery(
        idempotency_key=idempotency_key,
        text="edited via transport",
        payload={"operator": "test"},
    )
    deleted = gateway.delete_delivery(
        idempotency_key=idempotency_key,
        payload={"reason": "cleanup"},
    )
    repeated_delete = gateway.delete_delivery(idempotency_key=idempotency_key)

    assert edited.platform_state == "edited"
    assert edited.edit_revision == 1
    assert edited.text == "edited via transport"
    assert transport.edited[0]["text"] == "edited via transport"
    assert deleted.platform_state == "deleted"
    assert repeated_delete.id == deleted.id
    assert len(transport.deleted) == 1
    assert deleted.platform_payload == {
        "platform_message_id": delivered[0].platform_message_id,
        "text": "edited via transport",
        "operator": "test",
        "reason": "cleanup",
    }


def test_bot_gateway_edit_delete_delivery_api_calls_transport(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)
    app = create_app(control)
    app.state.bot_gateway = gateway
    client = TestClient(app)

    delivered = client.post(
        "/api/v1/bot-gateway/deliver-session-events",
        json={
            "session_id": session_id,
            "chat_context_id": context.id,
            "after_seq": 1,
        },
    )
    idempotency_key = delivered.json()[0]["idempotency_key"]
    edited = client.post(
        "/api/v1/bot-gateway/deliveries/edit",
        json={
            "idempotency_key": idempotency_key,
            "text": "edited api message",
            "payload": {"source": "api"},
        },
    )
    deleted = client.post(
        "/api/v1/bot-gateway/deliveries/delete",
        json={
            "idempotency_key": idempotency_key,
            "payload": {"source": "api-delete"},
        },
    )

    assert edited.status_code == 200
    assert edited.json()["platform_state"] == "edited"
    assert edited.json()["text"] == "edited api message"
    assert deleted.status_code == 200
    assert deleted.json()["platform_state"] == "deleted"
    assert transport.edited[0]["idempotency_key"].endswith(":edit:1")
    assert transport.deleted[0]["idempotency_key"].endswith(":delete")


def test_bot_gateway_delivery_results_api_reports_missing_record():
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/bot-gateway/delivery-results",
        json={"idempotency_key": "missing", "action": "acknowledge"},
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"


def test_bot_gateway_websocket_fans_out_rendered_events(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=1&idle_timeout_seconds=0"
    ) as websocket:
        frame = websocket.receive_json()
        idle = websocket.receive_json()

    assert frame["type"] == "bot.render.create"
    assert frame["seq"] == 2
    assert frame["session_id"] == session_id
    assert frame["chat_context_id"] == context.id
    assert frame["platform"] == "onebot.v11"
    assert frame["chat"]["chat_space_id"] == "group-bot"
    assert frame["event"]["type"] == "turn.queued"
    assert len(frame["messages"]) == 1
    assert frame["messages"][0]["idempotency_key"] == (
        f"onebot.v11:{context.id}:{frame['event_id']}:0"
    )
    assert "任务已排队" in frame["messages"][0]["text"]
    assert frame["actions"] == []
    assert idle == {"type": "idle_timeout", "last_seq": frame["seq"]}


def test_bot_gateway_websocket_exposes_stable_update_keys_for_assistant_deltas(
    tmp_path,
):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    turn_event = next(
        event
        for event in control.repository.list_events(session_id=session_id)
        if event.type == "turn.queued"
    )
    control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="assistant-delta-one",
        session_id=session_id,
        project_id=turn_event.project_id,
        turn_id=turn_event.turn_id,
        payload={"text": "hel"},
    )
    control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="assistant-delta-two",
        session_id=session_id,
        project_id=turn_event.project_id,
        turn_id=turn_event.turn_id,
        payload={"text": "lo"},
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=2&idle_timeout_seconds=0"
    ) as websocket:
        first_frame = websocket.receive_json()
        second_frame = websocket.receive_json()

    assert first_frame["event"]["type"] == "assistant.delta"
    assert second_frame["event"]["type"] == "assistant.delta"
    assert first_frame["messages"][0]["text"] == "hel"
    assert second_frame["messages"][0]["text"] == "lo"
    assert first_frame["document"]["update_key"] == (
        f"session:{session_id}:assistant:{turn_event.turn_id}"
    )
    assert second_frame["document"]["update_key"] == first_frame["document"]["update_key"]
    assert second_frame["messages"][0]["update_key"] == first_frame["messages"][0][
        "update_key"
    ]
    assert second_frame["messages"][0]["idempotency_key"] != first_frame["messages"][0][
        "idempotency_key"
    ]


def test_bot_gateway_websocket_includes_button_action_descriptors(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Allow deployment?",
        required_votes=1,
        trace_id="bot-ws-actions",
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=2&idle_timeout_seconds=0"
    ) as websocket:
        frame = websocket.receive_json()

    assert frame["type"] == "bot.render.create"
    assert frame["event"]["type"] == "approval.requested"
    assert [action["label"] for action in frame["actions"]] == ["批准一次", "拒绝"]
    assert frame["actions"][0]["style"] == "primary"
    assert frame["actions"][0]["payload"]["command"] == (
        f"/agent approve {interaction.id} once"
    )
    assert frame["actions"][0]["callback_data"] == f"/agent approve {interaction.id} once"
    assert f"/agent approve {interaction.id} once" in frame["messages"][0]["text"]


def test_bot_gateway_websocket_includes_question_select_descriptors(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.QUESTION,
        prompt="Which environment?",
        options=["staging", "production"],
        trace_id="bot-ws-select-actions",
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=2&idle_timeout_seconds=0"
    ) as websocket:
        frame = websocket.receive_json()

    assert frame["type"] == "bot.render.create"
    assert frame["event"]["type"] == "question.requested"
    assert [action["type"] for action in frame["actions"]] == ["select"]
    assert frame["actions"][0]["command_template"] == (
        f"/agent answer {interaction.id} {{answer}}"
    )
    assert frame["actions"][0]["input"]["name"] == "answer"
    assert frame["actions"][0]["options"] == [
        {"label": "staging", "value": "staging"},
        {"label": "production", "value": "production"},
    ]
    assert "1. staging" in frame["messages"][0]["text"]


def test_bot_gateway_websocket_includes_plan_action_descriptors(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.PLAN,
        prompt="Plan: run tests, deploy, then monitor.",
        trace_id="bot-ws-plan-actions",
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=2&idle_timeout_seconds=0"
    ) as websocket:
        frame = websocket.receive_json()

    assert frame["type"] == "bot.render.create"
    assert frame["event"]["type"] == "plan.requested"
    assert [action["label"] for action in frame["actions"]] == [
        "批准计划",
        "要求修改",
        "查看计划",
        "取消计划",
    ]
    assert [action["style"] for action in frame["actions"]] == [
        "primary",
        "default",
        "default",
        "danger",
    ]
    assert [action["type"] for action in frame["actions"]] == [
        "button",
        "modal",
        "button",
        "button",
    ]
    assert frame["actions"][0]["payload"]["command"] == (
        f"/agent plan approve {interaction.id}"
    )
    assert frame["actions"][1]["command_template"] == (
        f"/agent plan revise {interaction.id} {{feedback}}"
    )
    assert frame["actions"][1]["input"]["name"] == "feedback"
    assert frame["actions"][3]["callback_data"] == (
        f"/agent plan cancel {interaction.id}"
    )
    assert f"/agent plan revise {interaction.id} <feedback>" in frame["messages"][0]["text"]


def test_bot_gateway_websocket_preserves_interaction_ack_frame_type(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    event = control.emit_event(
        event_type="bot.interaction.ack",
        source=SemanticEventSource.BOT_GATEWAY,
        trace_id="bot-ws-ack",
        session_id=session_id,
        payload={
            "platform": "onebot.v11",
            "bot_instance_id": context.bot_instance_id,
            "chat_context_id": context.id,
            "chat_space_id": context.chat_space_id,
            "actor_id": "onebot:20002",
            "platform_event_id": "callback-ack-1",
            "interaction_kind": "action",
            "canonical_command": "approval.vote",
        },
        idempotency_key="onebot:callback-ack-1:bot-interaction-ack",
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=2&idle_timeout_seconds=0"
    ) as websocket:
        frame = websocket.receive_json()

    assert frame["type"] == "bot.interaction.ack"
    assert frame["event_id"] == event.id
    assert frame["event"]["type"] == "bot.interaction.ack"
    assert frame["event"]["payload"]["canonical_command"] == "approval.vote"
    assert frame["document"]["visibility"] == "operators"
    assert "Bot 交互已确认" in frame["messages"][0]["text"]
    assert frame["messages"][0]["idempotency_key"] == (
        f"onebot.v11:{context.id}:{event.id}:0"
    )


def test_bot_gateway_websocket_emits_render_update_and_delete(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)
    delivered = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )
    delivery_key = delivered[0].idempotency_key

    gateway.edit_delivery(
        idempotency_key=delivery_key,
        text="edited websocket text",
        payload={"source": "test-edit"},
    )
    gateway.delete_delivery(
        idempotency_key=delivery_key,
        payload={"source": "test-delete"},
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=2&idle_timeout_seconds=0"
    ) as websocket:
        update_frame = websocket.receive_json()
        delete_frame = websocket.receive_json()

    assert update_frame["type"] == "bot.render.update"
    assert update_frame["event"]["payload"]["delivery_idempotency_key"] == delivery_key
    assert update_frame["event"]["payload"]["text"] == "edited websocket text"
    assert update_frame["event"]["payload"]["edit_revision"] == 1
    assert update_frame["event"]["payload"]["original_event_type"] == "turn.queued"
    assert update_frame["messages"][0]["idempotency_key"] == (
        f"onebot.v11:{context.id}:{update_frame['event_id']}:0"
    )
    assert delete_frame["type"] == "bot.render.delete"
    assert delete_frame["event"]["payload"]["delivery_idempotency_key"] == delivery_key
    assert delete_frame["event"]["payload"]["platform_state"] == "deleted"
    assert delete_frame["event"]["payload"]["platform_payload"]["source"] == "test-delete"


def test_bot_gateway_websocket_requires_token_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBRIDGE_WS_TOKEN", "secret")
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    client = TestClient(create_app(control))
    url = (
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id={context.id}"
        "&after_seq=1&idle_timeout_seconds=0"
    )

    with client.websocket_connect(url) as websocket:
        denied = websocket.receive_json()

    assert denied["type"] == "error"
    assert denied["error"]["error_code"] == "PERMISSION_DENIED"

    with client.websocket_connect(f"{url}&token=secret") as websocket:
        frame = websocket.receive_json()

    assert frame["type"] == "bot.render.create"
    assert frame["event"]["type"] == "turn.queued"


def test_bot_gateway_websocket_reports_missing_chat_context(tmp_path):
    control = ControlPlane()
    _, session_id = create_session_with_turn(control, tmp_path)
    client = TestClient(create_app(control))

    with client.websocket_connect(
        "/api/v1/bot-gateway/session-events/ws"
        f"?session_id={session_id}&chat_context_id=missing"
    ) as websocket:
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert message["error"]["error_code"] == "NOT_FOUND"


def test_bot_gateway_notification_websocket_streams_cross_stream_delivery():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-notification-ws",
    )
    source_event = control.emit_event(
        event_type="device_identity.certificates_scanned",
        source=SemanticEventSource.CONTROL_PLANE,
        trace_id="notification-ws-scan",
        payload={
            "total_device_count": 1,
            "action_required_count": 1,
            "status_counts": {"expired": 1},
            "warning_days": 14,
            "scanned_at": "2026-06-26T00:00:00Z",
            "action_required_devices": [
                {
                    "device_id": "notify-expired-device",
                    "certificate_health_status": "expired",
                    "expired_count": 1,
                }
            ],
        },
    )
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)
    records = gateway.deliver_events(
        chat_context_id=context.id,
        event_type="device_identity.certificates_scanned",
        trace_id="notification-ws-scan",
    )
    notifications = control.repository.list_semantic_events(
        event_type="bot.notification",
        trace_id="notification-ws-scan",
    )
    app = create_app(control)
    app.state.bot_gateway = gateway
    client = TestClient(app)

    with client.websocket_connect(
        "/api/v1/bot-gateway/notifications/ws"
        f"?chat_context_id={context.id}&idle_timeout_seconds=0"
    ) as websocket:
        frame = websocket.receive_json()
        idle = websocket.receive_json()

    assert len(records) == 1
    assert len(notifications) == 1
    assert notifications[0].payload["source_event_id"] == source_event.id
    assert frame["type"] == "bot.notification"
    assert frame["session_id"] == ""
    assert frame["chat_context_id"] == context.id
    assert frame["event_id"] == notifications[0].id
    assert frame["event"]["payload"]["source_event_type"] == (
        "device_identity.certificates_scanned"
    )
    assert frame["event"]["payload"]["delivery_records"][0]["idempotency_key"] == (
        records[0].idempotency_key
    )
    assert "Bot 通知" in frame["messages"][0]["text"]
    assert idle == {"type": "idle_timeout", "last_seq": frame["seq"]}


def test_bot_gateway_notification_websocket_replays_after_event_id(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    system_event = control.emit_event(
        event_type="device_identity.certificates_scanned",
        source=SemanticEventSource.CONTROL_PLANE,
        trace_id="notification-ws-replay-system",
        payload={
            "total_device_count": 1,
            "action_required_count": 1,
            "status_counts": {"expired": 1},
            "warning_days": 14,
            "scanned_at": "2026-06-26T00:00:00Z",
        },
    )
    gateway = BotGatewayService(control, transport=InMemoryBotTransport())
    gateway.deliver_events(
        chat_context_id=context.id,
        event_type="device_identity.certificates_scanned",
        trace_id=system_event.trace_id,
    )
    gateway.deliver_events(
        chat_context_id=context.id,
        session_id=session_id,
        event_type="turn.queued",
    )
    notifications = [
        event for event in control.repository.semantic_events if event.type == "bot.notification"
    ]
    app = create_app(control)
    app.state.bot_gateway = gateway
    client = TestClient(app)

    with client.websocket_connect(
        "/api/v1/bot-gateway/notifications/ws"
        f"?chat_context_id={context.id}"
        f"&after_event_id={notifications[0].id}"
        "&idle_timeout_seconds=0"
    ) as websocket:
        frame = websocket.receive_json()
        idle = websocket.receive_json()

    assert len(notifications) == 2
    assert notifications[0].payload["source_event_type"] == (
        "device_identity.certificates_scanned"
    )
    assert notifications[1].payload["source_event_type"] == "turn.queued"
    assert frame["type"] == "bot.notification"
    assert frame["event_id"] == notifications[1].id
    assert frame["session_id"] == session_id
    assert frame["chat_context_id"] == context.id
    assert frame["event"]["payload"]["source_event_type"] == "turn.queued"
    assert idle == {"type": "idle_timeout", "last_seq": frame["seq"]}


def test_bot_gateway_api_delivers_filtered_semantic_events():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-security-api",
    )
    control.emit_event(
        event_type="device_identity.certificates_scanned",
        source=SemanticEventSource.CONTROL_PLANE,
        trace_id="security-scan-api",
        payload={
            "total_device_count": 1,
            "action_required_count": 1,
            "status_counts": {"expired": 1},
            "warning_days": 14,
            "scanned_at": "2026-06-26T00:00:00Z",
            "action_required_devices": [
                {
                    "device_id": "api-expired-device",
                    "certificate_health_status": "expired",
                    "expired_count": 1,
                }
            ],
        },
    )
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)
    app = create_app(control)
    app.state.bot_gateway = gateway
    client = TestClient(app)

    response = client.post(
        "/api/v1/bot-gateway/deliver-events",
        json={
            "chat_context_id": context.id,
            "event_type": "device_identity.certificates_scanned",
            "trace_id": "security-scan-api",
        },
    )
    duplicate_response = client.post(
        "/api/v1/bot-gateway/deliver-events",
        json={
            "chat_context_id": context.id,
            "event_type": "device_identity.certificates_scanned",
            "trace_id": "security-scan-api",
        },
    )
    unfiltered_response = client.post(
        "/api/v1/bot-gateway/deliver-events",
        json={"chat_context_id": context.id},
    )

    assert response.status_code == 200
    assert [record["status"] for record in response.json()] == ["sent"]
    assert duplicate_response.status_code == 200
    assert [record["status"] for record in duplicate_response.json()] == [
        "skipped_duplicate"
    ]
    assert unfiltered_response.status_code == 400
    assert unfiltered_response.json()["error_code"] == "COMMAND_ARGUMENT_INVALID"
    assert len(transport.sent) == 1
    assert "api-expired-device" in transport.sent[0]["text"]


def test_certificate_scan_worker_notifies_configured_bot_context():
    control = ControlPlane()
    admin = Actor(id="security-admin", roles={"admin"})
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-cert-alerts",
    )
    control.upsert_device_identity(
        actor=admin,
        device_id="unknown-certificate-device",
        device_key="managed-secret",
        certificate_fingerprints={"SHA256:AA:BB:CC"},
        trace_id="cert-notify-device-create",
    )
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)
    worker = DeviceCertificateScanWorker(
        control,
        bot_gateway=gateway,
        notify_chat_context_ids=(context.id,),
        warning_days=7,
    )

    result = worker.run_once(trace_id="cert-scan-notify")
    status = worker.status()

    assert result["action_required_count"] == 1
    assert status["last_notification_error"] is None
    assert status["last_notification_record_count"] == 1
    assert status["last_notification_status_counts"] == {"sent": 1}
    assert status["notify_chat_context_ids"] == [context.id]
    assert len(transport.sent) == 1
    assert "设备证书扫描" in transport.sent[0]["text"]
    assert "unknown-certificate-device" in transport.sent[0]["text"]


def test_certificate_scan_worker_skips_notification_without_action_required():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-cert-alerts",
    )
    transport = InMemoryBotTransport()
    gateway = BotGatewayService(control, transport=transport)
    worker = DeviceCertificateScanWorker(
        control,
        bot_gateway=gateway,
        notify_chat_context_ids=(context.id,),
    )

    result = worker.run_once(trace_id="cert-scan-clear")
    status = worker.status()

    assert result["action_required_count"] == 0
    assert status["last_notification_record_count"] == 0
    assert status["last_notification_status_counts"] == {}
    assert transport.sent == []


def test_bot_retry_worker_api_reports_status_and_runs_once(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_turn(control, tmp_path)
    transport = FlakyTransport(fail_times=1)
    gateway = BotGatewayService(control, transport=transport, retry_base_seconds=0)
    app = create_app(control)
    app.state.bot_gateway = gateway
    app.state.bot_retry_worker = BotDeliveryRetryWorker(gateway, batch_size=10)
    client = TestClient(app)

    failed = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
        after_seq=1,
    )
    status_response = client.get("/api/v1/bot-gateway/retry-worker")
    run_response = client.post(
        "/api/v1/bot-gateway/retry-worker/run-once",
        json={"chat_context_id": context.id, "limit": 10},
    )

    assert failed[0].status == BotDeliveryStatus.FAILED
    assert status_response.status_code == 200
    assert status_response.json()["running"] is False
    assert run_response.status_code == 200
    assert run_response.json()["worker"]["last_record_count"] == 1
    assert run_response.json()["records"][0]["status"] == "sent"
    assert len(transport.sent) == 1


def test_bot_rate_limit_config_is_exposed_from_environment(monkeypatch):
    monkeypatch.setenv(
        "AGENTBRIDGE_BOT_RATE_LIMITS",
        "onebot.v11=20/60,plain_text=100/10",
    )
    client = TestClient(create_app())

    response = client.get("/api/v1/bot-gateway/rate-limits")

    assert response.status_code == 200
    assert response.json()["policies"] == [
        {"platform": "onebot.v11", "capacity": 20, "window_seconds": 60.0},
        {"platform": "plain_text", "capacity": 100, "window_seconds": 10.0},
    ]


def test_bot_retry_worker_can_autostart_from_environment(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_BOT_RETRY_WORKER_ENABLED", "true")
    monkeypatch.setenv("AGENTBRIDGE_BOT_RETRY_INTERVAL_SECONDS", "60")
    app = create_app()

    with TestClient(app) as client:
        running_status = client.get("/api/v1/bot-gateway/retry-worker")
        assert running_status.status_code == 200
        assert running_status.json()["enabled"] is True
        assert running_status.json()["running"] is True

    assert app.state.bot_retry_worker.is_running() is False


def test_bot_delivery_admin_ui_serves_dashboard():
    client = TestClient(create_app())

    response = client.get("/admin/bot-delivery")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AgentBridge Bot Delivery" in html
    assert "/api/v1/bot-gateway/deliveries" in html
    assert "/api/v1/bot-gateway/deliveries/edit" in html
    assert "/api/v1/bot-gateway/deliveries/delete" in html
    assert "/api/v1/bot-gateway/retry-worker" in html
    assert "/api/v1/bot-gateway/capabilities" in html
    assert "/api/v1/bot-gateway/rate-limits" in html
    assert "/api/v1/events?event_type=bot.command_registration.result&limit=20" in html
    assert "async function retryDue()" in html
    assert "async function editSelected()" in html
    assert "async function deleteSelected()" in html
    assert "function renderCommandRegistrations(events)" in html
    assert "function renderCapabilities(capabilities)" in html
    assert "cap-onebot-edit" in html
    assert "command-registration-results" in html
