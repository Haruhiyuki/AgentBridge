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
from agentbridge.domain import Actor, AgentBridgeError, BotPlatform, ErrorCode
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
