from __future__ import annotations

from collections import Counter
from datetime import datetime
from threading import Event, RLock, Thread, current_thread

from agentbridge.bot_gateway import BotGatewayService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, BotDeliveryRecord, BotPlatform, utc_now


class DeviceCertificateScanWorker:
    """Background scheduler for managed device certificate health scans."""

    def __init__(
        self,
        control: ControlPlane,
        *,
        enabled: bool = False,
        interval_seconds: float = 3600.0,
        warning_days: int = 14,
        include_revoked: bool = False,
        actor_id: str = "certificate-scan-worker",
        bot_gateway: BotGatewayService | None = None,
        notify_chat_context_ids: tuple[str, ...] = (),
        notify_platform: BotPlatform = BotPlatform.ONEBOT_V11,
        notify_only_action_required: bool = True,
    ) -> None:
        self.control = control
        self.enabled = enabled
        self.interval_seconds = max(float(interval_seconds), 1.0)
        self.warning_days = max(int(warning_days), 1)
        self.include_revoked = include_revoked
        self.actor_id = actor_id.strip() or "certificate-scan-worker"
        self.bot_gateway = bot_gateway
        self.notify_chat_context_ids = tuple(
            chat_context_id.strip()
            for chat_context_id in notify_chat_context_ids
            if chat_context_id.strip()
        )
        self.notify_platform = notify_platform
        self.notify_only_action_required = notify_only_action_required
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.started_at: datetime | None = None
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.last_notification_error: str | None = None
        self.last_notification_record_count = 0
        self.last_notification_status_counts: dict[str, int] = {}
        self.last_action_required_count = 0
        self.last_total_device_count = 0
        self.last_status_counts: dict[str, int] = {}
        self.run_count = 0

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self.started_at = utc_now()
            self._thread = Thread(
                target=self._run_loop,
                name="agentbridge-device-certificate-scan-worker",
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
        actor: Actor | None = None,
        warning_days: int | None = None,
        include_revoked: bool | None = None,
        trace_id: str = "device-certificate-scan-worker",
    ) -> dict[str, object]:
        scan_actor = actor or Actor(id=self.actor_id, roles={"admin"})
        scan_warning_days = max(int(warning_days or self.warning_days), 1)
        scan_include_revoked = (
            include_revoked if include_revoked is not None else self.include_revoked
        )
        with self._lock:
            self.run_count += 1
            self.last_run_at = utc_now()
        try:
            result = self.control.scan_device_identity_certificates(
                actor=scan_actor,
                warning_days=scan_warning_days,
                include_revoked=scan_include_revoked,
                trace_id=trace_id,
            )
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)
                self.last_action_required_count = 0
                self.last_total_device_count = 0
                self.last_status_counts = {}
                self.last_notification_error = None
                self.last_notification_record_count = 0
                self.last_notification_status_counts = {}
            return {}
        try:
            notification_records = self._deliver_notifications(result, trace_id=trace_id)
            notification_error = None
        except Exception as exc:
            notification_records = []
            notification_error = str(exc)
        with self._lock:
            self.last_error = None
            self.last_action_required_count = int(result["action_required_count"])
            self.last_total_device_count = int(result["total_device_count"])
            self.last_status_counts = {
                str(key): int(value)
                for key, value in dict(result["status_counts"]).items()
            }
            self.last_notification_error = notification_error
            self.last_notification_record_count = len(notification_records)
            self.last_notification_status_counts = dict(
                Counter(record.status.value for record in notification_records)
            )
        return result

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "warning_days": self.warning_days,
                "include_revoked": self.include_revoked,
                "actor_id": self.actor_id,
                "notify_chat_context_ids": list(self.notify_chat_context_ids),
                "notify_platform": self.notify_platform.value,
                "notify_only_action_required": self.notify_only_action_required,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
                "last_error": self.last_error,
                "last_notification_error": self.last_notification_error,
                "last_notification_record_count": self.last_notification_record_count,
                "last_notification_status_counts": self.last_notification_status_counts,
                "last_action_required_count": self.last_action_required_count,
                "last_total_device_count": self.last_total_device_count,
                "last_status_counts": self.last_status_counts,
                "run_count": self.run_count,
            }

    def _deliver_notifications(
        self,
        result: dict[str, object],
        *,
        trace_id: str,
    ) -> list[BotDeliveryRecord]:
        if self.bot_gateway is None or not self.notify_chat_context_ids:
            return []
        action_required_count = int(result.get("action_required_count") or 0)
        if self.notify_only_action_required and action_required_count <= 0:
            return []
        records: list[BotDeliveryRecord] = []
        for chat_context_id in self.notify_chat_context_ids:
            records.extend(
                self.bot_gateway.deliver_events(
                    chat_context_id=chat_context_id,
                    platform=self.notify_platform,
                    event_type="device_identity.certificates_scanned",
                    trace_id=trace_id,
                    limit=1,
                )
            )
        return records

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.interval_seconds)
