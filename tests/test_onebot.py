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
from agentbridge.domain import Actor, AgentBridgeError, BotPlatform, ErrorCode
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
