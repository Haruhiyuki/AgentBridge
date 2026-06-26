from __future__ import annotations

import urllib.error
from dataclasses import dataclass, field
from email.message import Message
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.bot_gateway import BotGatewayService
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    BotPlatform,
    ErrorCode,
    InteractionStatus,
    InteractionType,
    Visibility,
)
from agentbridge.onebot import OneBotInboundAdapter, OneBotV11HTTPTransport, onebot_text_payload


@dataclass
class FakePoster:
    calls: list[dict[str, Any]] = field(default_factory=list)
    response: dict[str, Any] = field(
        default_factory=lambda: {"retcode": 0, "data": {"message_id": 42}}
    )

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self.response


def create_session_with_event(control: ControlPlane, tmp_path):
    commands = CommandService(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    context = control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    commands.execute(
        commands.parse(
            raw_text=f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="onebot-project",
            trace_id="onebot-project",
        )
    )
    session_result = commands.execute(
        commands.parse(
            raw_text="/agent session new OneBot Delivery",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="onebot-session",
            trace_id="onebot-session",
        )
    )
    return context, session_result.data["session_id"]


def test_onebot_text_payload_selects_group_or_private_route():
    control = ControlPlane()
    group_context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    private_context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="private",
        user_id="20002",
    )

    assert onebot_text_payload(group_context, "hello") == (
        "send_group_msg",
        {"group_id": "10001", "message": "hello"},
    )
    assert onebot_text_payload(private_context, "hello") == (
        "send_private_msg",
        {"user_id": "20002", "message": "hello"},
    )


def test_onebot_transport_posts_payload_with_auth_and_idempotency():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    poster = FakePoster()
    transport = OneBotV11HTTPTransport(
        endpoint="http://127.0.0.1:5700/",
        access_token="token",
        poster=poster,
    )

    message_id = transport.send_text(
        platform=BotPlatform.ONEBOT_V11,
        chat_context_id=context.id,
        chat_context=context,
        text="hello",
        idempotency_key="idem-1",
    )

    assert message_id == "onebot:42"
    assert poster.calls == [
        {
            "url": "http://127.0.0.1:5700/send_group_msg",
            "payload": {"group_id": "10001", "message": "hello"},
            "headers": {
                "authorization": "Bearer token",
                "x-agentbridge-idempotency-key": "idem-1",
            },
        }
    ]


def test_onebot_transport_deletes_message_with_auth_and_idempotency():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    poster = FakePoster(response={"retcode": 0, "data": {}})
    transport = OneBotV11HTTPTransport(
        endpoint="http://127.0.0.1:5700/",
        access_token="token",
        poster=poster,
    )

    payload = transport.delete_message(
        platform=BotPlatform.ONEBOT_V11,
        chat_context_id=context.id,
        chat_context=context,
        platform_message_id="onebot:42",
        idempotency_key="idem-delete",
    )

    assert payload["platform_message_id"] == "onebot:42"
    assert poster.calls == [
        {
            "url": "http://127.0.0.1:5700/delete_msg",
            "payload": {"message_id": 42},
            "headers": {
                "authorization": "Bearer token",
                "x-agentbridge-idempotency-key": "idem-delete",
            },
        }
    ]


def test_onebot_transport_reports_edit_as_unsupported():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    transport = OneBotV11HTTPTransport(endpoint="http://127.0.0.1:5700")

    with pytest.raises(AgentBridgeError) as exc_info:
        transport.edit_text(
            platform=BotPlatform.ONEBOT_V11,
            chat_context_id=context.id,
            chat_context=context,
            platform_message_id="onebot:42",
            text="edited",
            idempotency_key="idem-edit",
        )

    assert exc_info.value.code == ErrorCode.PLATFORM_CAPABILITY_MISSING


def test_onebot_transport_rejects_failed_retcode():
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    transport = OneBotV11HTTPTransport(
        endpoint="http://127.0.0.1:5700",
        poster=FakePoster(response={"retcode": 1400, "msg": "failed"}),
    )

    with pytest.raises(AgentBridgeError):
        transport.send_text(
            platform=BotPlatform.ONEBOT_V11,
            chat_context_id=context.id,
            chat_context=context,
            text="hello",
            idempotency_key="idem-1",
        )


def test_onebot_http_poster_exposes_retry_after_from_rate_limit(monkeypatch):
    control = ControlPlane()
    context = control.get_or_create_chat_context(
        bot_instance_id="bot",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    transport = OneBotV11HTTPTransport(endpoint="http://127.0.0.1:5700")

    def rate_limited(request, timeout):
        headers = Message()
        headers["Retry-After"] = "9"
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            headers,
            BytesIO(b'{"retcode": 429}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", rate_limited)

    with pytest.raises(AgentBridgeError) as exc_info:
        transport.send_text(
            platform=BotPlatform.ONEBOT_V11,
            chat_context_id=context.id,
            chat_context=context,
            text="hello",
            idempotency_key="idem-429",
        )

    assert exc_info.value.code == ErrorCode.QUOTA_EXCEEDED
    assert exc_info.value.details["status_code"] == 429
    assert exc_info.value.details["retry_after_seconds"] == 9


def test_bot_gateway_can_deliver_through_onebot_transport(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_event(control, tmp_path)
    poster = FakePoster()
    transport = OneBotV11HTTPTransport(
        endpoint="http://127.0.0.1:5700",
        poster=poster,
    )
    gateway = BotGatewayService(control, transport=transport)

    records = gateway.deliver_session_events(
        session_id=session_id,
        chat_context_id=context.id,
    )

    assert len(records) == 1
    assert records[0].platform_message_id == "onebot:42"
    assert poster.calls[0]["url"] == "http://127.0.0.1:5700/send_group_msg"
    assert "OneBot Delivery" in poster.calls[0]["payload"]["message"]


def test_onebot_inbound_adapter_maps_group_message_to_command():
    adapter = OneBotInboundAdapter(bot_instance_id="bot-main", default_roles={"operator"})

    command = adapter.command_from_event(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 10001,
            "user_id": 20002,
            "message_id": 30003,
            "raw_message": "/agent health",
        }
    )

    assert command is not None
    assert command.raw_text == "/agent health"
    assert command.actor.id == "onebot:20002"
    assert command.actor.roles == {"operator"}
    assert command.chat_space_id == "10001"
    assert command.user_id is None
    assert command.idempotency_key == "onebot:30003"


def test_onebot_inbound_adapter_extracts_segments_reply_and_private_context():
    adapter = OneBotInboundAdapter(bot_instance_id="bot-main")

    command = adapter.command_from_event(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": "20002",
            "message_id": "30003",
            "message": [
                {"type": "reply", "data": {"id": "old-message"}},
                {"type": "text", "data": {"text": "/agent "}},
                {"type": "text", "data": {"text": "health"}},
            ],
        }
    )

    assert command is not None
    assert command.raw_text == "/agent health"
    assert command.chat_space_id == "private:20002"
    assert command.user_id == "20002"
    assert command.reply_message_id == "old-message"


def test_onebot_inbound_adapter_maps_action_callback_descriptor_to_command():
    adapter = OneBotInboundAdapter(bot_instance_id="bot-main", default_roles={"approver"})

    command = adapter.command_from_event(
        {
            "post_type": "notice",
            "notice_type": "button_clicked",
            "group_id": 10001,
            "user_id": 20002,
            "event_id": "callback-1",
            "data": {
                "action_id": "approve-int_1",
                "payload": {
                    "command": "/agent approve int_1 once",
                    "reply_message_id": "rendered-message-1",
                },
            },
        }
    )

    assert command is not None
    assert command.raw_text == "/agent approve int_1 once"
    assert command.actor.id == "onebot:20002"
    assert command.actor.roles == {"approver"}
    assert command.chat_space_id == "10001"
    assert command.user_id is None
    assert command.reply_message_id == "rendered-message-1"
    assert command.idempotency_key == "onebot:callback-1"


def test_onebot_inbound_adapter_rejects_action_callback_without_user_id():
    adapter = OneBotInboundAdapter(bot_instance_id="bot-main")

    with pytest.raises(AgentBridgeError) as exc_info:
        adapter.command_from_event(
            {
                "post_type": "notice",
                "notice_type": "button_clicked",
                "group_id": 10001,
                "event_id": "callback-1",
                "callback_data": "/agent approve int_1 once",
            }
        )

    assert exc_info.value.code == ErrorCode.COMMAND_ARGUMENT_INVALID
    assert "user_id" in exc_info.value.message


def test_onebot_inbound_adapter_ignores_non_command_message():
    adapter = OneBotInboundAdapter(bot_instance_id="bot-main")

    assert (
        adapter.command_from_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30003,
                "raw_message": "hello",
            }
        )
        is None
    )


def test_onebot_events_api_ignores_non_commands_and_executes_commands():
    client = TestClient(create_app())

    ignored = client.post(
        "/api/v1/onebot/events",
        json={
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30003,
                "raw_message": "hello",
            }
        },
    )
    handled = client.post(
        "/api/v1/onebot/events",
        json={
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30004,
                "raw_message": "/agent health",
            }
        },
    )

    assert ignored.status_code == 200
    assert ignored.json() == {"handled": False}
    assert handled.status_code == 200
    assert handled.json()["handled"] is True
    assert handled.json()["result"]["canonical_command"] == "health"


def test_onebot_events_api_executes_action_callback_with_click_actor(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    context = control.get_or_create_chat_context(
        bot_instance_id="onebot-http",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Callback Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session.id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Allow direct OneBot callback?",
        required_votes=1,
        trace_id="approval",
        chat_context_id=context.id,
    )
    client = TestClient(create_app(control_plane=control))

    response = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["approver"],
            "event": {
                "post_type": "notice",
                "notice_type": "button_clicked",
                "group_id": 10001,
                "user_id": 20002,
                "event_id": "callback-approve-1",
                "payload": {"command": f"/agent approve {interaction.id} once"},
            },
        },
    )

    stored = control.get_interaction(actor=maintainer, interaction_id=interaction.id)
    assert response.status_code == 200
    assert response.json()["handled"] is True
    assert response.json()["result"]["canonical_command"] == "approval.vote"
    ack_event = response.json()["ack_event"]
    assert ack_event["type"] == "bot.interaction.ack"
    assert ack_event["source"] == "bot_gateway"
    assert ack_event["session_id"] == session.id
    assert ack_event["interaction_id"] == interaction.id
    assert ack_event["idempotency_key"] == "onebot:callback-approve-1:bot-interaction-ack"
    assert ack_event["payload"]["interaction_kind"] == "action"
    assert ack_event["payload"]["actor_id"] == "onebot:20002"
    assert ack_event["payload"]["platform_event_id"] == "callback-approve-1"
    assert ack_event["payload"]["canonical_command"] == "approval.vote"
    assert stored.status == InteractionStatus.RESOLVED
    assert stored.votes == {"onebot:20002": True}

    repeated = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["approver"],
            "event": {
                "post_type": "notice",
                "notice_type": "button_clicked",
                "group_id": 10001,
                "user_id": 20002,
                "event_id": "callback-approve-1",
                "payload": {"command": f"/agent approve {interaction.id} once"},
            },
        },
    )
    ack_events = [
        event
        for event in control.repository.list_events(session_id=session.id, limit=20)
        if event.type == "bot.interaction.ack"
    ]
    assert repeated.status_code == 200
    assert repeated.json()["ack_event"]["id"] == ack_event["id"]
    assert len(ack_events) == 1


def test_onebot_events_api_executes_modal_plan_revision(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_event(control, tmp_path)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.PLAN,
        prompt="Plan: deploy directly.",
        trace_id="modal-plan",
        chat_context_id=context.id,
    )
    client = TestClient(create_app(control_plane=control))

    response = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["operator"],
            "event": {
                "post_type": "notice",
                "notice_type": "modal_submitted",
                "group_id": 10001,
                "user_id": 20002,
                "event_id": "modal-plan-1",
                "payload": {
                    "command_template": f"/agent plan revise {interaction.id} {{feedback}}",
                    "values": {
                        "feedback": "Use expand-contract migration first",
                    },
                },
            },
        },
    )

    stored = control.get_interaction(actor=maintainer, interaction_id=interaction.id)
    assert response.status_code == 200
    assert response.json()["result"]["canonical_command"] == "plan.revise"
    assert response.json()["ack_event"]["payload"]["interaction_kind"] == "modal"
    assert stored.status == InteractionStatus.RESOLVED
    assert stored.answer == "Use expand-contract migration first"


def test_onebot_events_api_executes_selection_answer(tmp_path):
    control = ControlPlane()
    context, session_id = create_session_with_event(control, tmp_path)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.QUESTION,
        prompt="Which environment?",
        options=["staging", "production"],
        trace_id="selection-question",
        chat_context_id=context.id,
    )
    client = TestClient(create_app(control_plane=control))

    response = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["operator"],
            "event": {
                "post_type": "notice",
                "notice_type": "selection_submitted",
                "group_id": 10001,
                "user_id": 20002,
                "event_id": "selection-question-1",
                "payload": {
                    "command_template": f"/agent answer {interaction.id} {{answer}}",
                    "selected_value": "production",
                },
            },
        },
    )

    stored = control.get_interaction(actor=maintainer, interaction_id=interaction.id)
    assert response.status_code == 200
    assert response.json()["result"]["canonical_command"] == "interaction.answer"
    assert response.json()["ack_event"]["payload"]["interaction_kind"] == "selection"
    assert stored.status == InteractionStatus.RESOLVED
    assert stored.answer == "production"


def test_onebot_reply_to_approval_delivery_infers_interaction_for_text_fallback(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    context = control.get_or_create_chat_context(
        bot_instance_id="onebot-http",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Reply Approval",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session.id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Allow reply approval?",
        required_votes=1,
        trace_id="approval",
        chat_context_id=context.id,
    )
    records = BotGatewayService(control).deliver_session_events(
        session_id=session.id,
        chat_context_id=context.id,
    )
    approval_message = next(record for record in records if "Allow reply approval?" in record.text)
    client = TestClient(create_app(control_plane=control))

    response = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["approver"],
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 40001,
                "reply_message_id": approval_message.platform_message_id,
                "raw_message": "/agent approve once",
            },
        },
    )

    stored = control.get_interaction(actor=maintainer, interaction_id=interaction.id)
    assert response.status_code == 200
    assert response.json()["result"]["canonical_command"] == "approval.vote"
    assert stored.status == InteractionStatus.RESOLVED
    assert stored.votes == {"onebot:20002": True}


def test_onebot_reply_to_question_delivery_infers_interaction_for_text_answer(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    context = control.get_or_create_chat_context(
        bot_instance_id="onebot-http",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Reply Question",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session.id,
        interaction_type=InteractionType.QUESTION,
        prompt="Which environment?",
        required_votes=1,
        trace_id="question",
        chat_context_id=context.id,
    )
    records = BotGatewayService(control).deliver_session_events(
        session_id=session.id,
        chat_context_id=context.id,
    )
    question_message = next(record for record in records if "Which environment?" in record.text)
    client = TestClient(create_app(control_plane=control))

    response = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["operator"],
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 40002,
                "message": [
                    {"type": "reply", "data": {"id": question_message.platform_message_id}},
                    {"type": "text", "data": {"text": "/agent answer staging"}},
                ],
            },
        },
    )

    stored = control.get_interaction(actor=maintainer, interaction_id=interaction.id)
    assert response.status_code == 200
    assert response.json()["result"]["canonical_command"] == "interaction.answer"
    assert stored.status == InteractionStatus.RESOLVED
    assert stored.answer == "staging"


def test_onebot_reply_to_plan_delivery_infers_interaction_for_revision(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    context = control.get_or_create_chat_context(
        bot_instance_id="onebot-http",
        platform="onebot.v11",
        chat_space_id="10001",
    )
    session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Reply Plan",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session.id,
        interaction_type=InteractionType.PLAN,
        prompt="Plan: deploy directly.",
        trace_id="plan",
        chat_context_id=context.id,
    )
    records = BotGatewayService(control).deliver_session_events(
        session_id=session.id,
        chat_context_id=context.id,
    )
    plan_message = next(record for record in records if "Plan: deploy directly." in record.text)
    client = TestClient(create_app(control_plane=control))

    response = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["operator"],
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 40003,
                "reply_message_id": plan_message.platform_message_id,
                "raw_message": "/agent plan revise Use expand-contract migration first",
            },
        },
    )

    stored = control.get_interaction(actor=maintainer, interaction_id=interaction.id)
    assert response.status_code == 200
    assert response.json()["result"]["canonical_command"] == "plan.revise"
    assert stored.status == InteractionStatus.RESOLVED
    assert stored.answer == "Use expand-contract migration first"


def test_managed_device_identity_requires_onebot_event_ingest_scope_for_events():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-onebot-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "onebot-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-onebot-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    cert_headers = {"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"}
    health_response = client.get("/api/v1/health", headers=key_headers)
    key_event_response = client.post(
        "/api/v1/onebot/events",
        json={
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30005,
                "raw_message": "/agent health",
            }
        },
        headers=key_headers,
    )
    cert_event_response = client.post(
        "/api/v1/onebot/events",
        json={
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30006,
                "raw_message": "/agent health",
            }
        },
        headers=cert_headers,
    )

    assert create_identity_response.status_code == 200
    assert health_response.status_code == 200
    assert key_event_response.status_code == 403
    assert cert_event_response.status_code == 403


def test_managed_device_identity_onebot_event_ingest_scope_allows_events():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "onebot-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "onebot_event_ingest"],
            "trace_id": "onebot-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "onebot-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    event_response = client.post(
        "/api/v1/onebot/events",
        json={
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 20002,
                "message_id": 30007,
                "raw_message": "/agent health",
            }
        },
        headers=headers,
    )

    assert create_identity_response.status_code == 200
    assert event_response.status_code == 200
    assert event_response.json()["handled"] is True
    assert event_response.json()["result"]["canonical_command"] == "health"


def test_onebot_events_api_uses_group_role_bindings_for_permissions(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "onebot-http",
        "platform": "onebot.v11",
        "chat_space_id": "10009",
    }
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    context = client.post("/api/v1/chat-contexts", json=chat).json()

    project_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "onebot-role-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new OneBot Roles",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "onebot-role-session",
        },
    )
    assert project_response.status_code == 200
    assert session_response.status_code == 200

    denied = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["member"],
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10009,
                "user_id": 20002,
                "message_id": 31001,
                "raw_message": "/agent ask before grant",
            },
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "PERMISSION_DENIED"

    grant_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/grant",
        json={
            "actor": maintainer,
            "target_actor_id": "onebot:20002",
            "roles": ["operator"],
            "trace_id": "onebot-role-grant",
        },
    )
    assert grant_response.status_code == 200

    handled = client.post(
        "/api/v1/onebot/events",
        json={
            "default_roles": ["member"],
            "event": {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10009,
                "user_id": 20002,
                "message_id": 31002,
                "raw_message": "/agent ask after grant",
            },
        },
    )
    assert handled.status_code == 200
    assert handled.json()["handled"] is True
    assert handled.json()["result"]["canonical_command"] == "turn.enqueue"
