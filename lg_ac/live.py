"""Live status updates pushed by LG (MQTT), so changes made elsewhere show up right away.

LG's flow: register this client, get a certificate for it, connect to LG's AWS IoT endpoint
with that certificate, and subscribe to the device's events. The certificate is cached on
disk (readable only by the user) so this happens once, not on every start.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Callable

import aiohttp
from thinqconnect import ThinQApi, ThinQAPIException

_LOGGER = logging.getLogger(__name__)
_NAMESPACE = uuid.UUID("6f1c2a8e-1f7b-4c55-9d0e-5c3b7a8e4d21")
_FILES = ("certificate.pem", "private-key.pem", "root-ca.pem", "topic")


def live_client_id(client_id: str, role: str) -> str:
    """A stable client ID per role ("app", "cli"): two connections sharing one ID would kick
    each other off LG's server."""
    return str(uuid.uuid5(_NAMESPACE, f"{client_id}:{role}"))


def parse_message(payload: bytes, device_id: str) -> dict | None:
    """The status report in a pushed message for `device_id`, or None if it isn't one."""
    try:
        message = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict) or message.get("deviceId") != device_id:
        return None
    if message.get("pushType", "DEVICE_STATUS") != "DEVICE_STATUS":
        return None
    report = message.get("report")
    return report if isinstance(report, dict) and report else None


class LiveUpdates:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        country_code: str,
        client_id: str,
        device_id: str,
        on_report: Callable[[dict], None],
        store_dir: Path,
        on_connection_change: Callable[[bool], None] | None = None,
        client_factory=None,
    ):
        self.api = ThinQApi(session=session, access_token=access_token, country_code=country_code, client_id=client_id)
        self.client_id = client_id
        self.device_id = device_id
        self.on_report = on_report
        self.on_connection_change = on_connection_change
        self.store_dir = store_dir
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client_factory = client_factory

    @staticmethod
    def _default_factory():
        # Needs the AWS IoT libraries; without them live updates are simply off.
        from thinqconnect.mqtt_client import ThinQMQTTClient

        return ThinQMQTTClient

    async def start(self) -> bool:
        """Connect and subscribe. Returns False (without raising) if live updates aren't available."""
        self._loop = asyncio.get_running_loop()
        try:
            if self._client_factory is None:
                self._client_factory = self._default_factory()
            self._client = await self._client_factory(
                thinq_api=self.api,
                client_id=self.client_id,
                on_message_received=self._on_message,
                on_connection_interrupted=lambda *a, **k: self._connection_changed(False),
                on_connection_success=lambda *a, **k: self._connection_changed(True),
                on_connection_failure=lambda *a, **k: self._connection_changed(False),
                on_connection_closed=lambda *a, **k: self._connection_changed(False),
            )
            if not await self._connect(use_cache=True):
                # A stale cached certificate is the usual cause; get a fresh one and retry once.
                if not await self._connect(use_cache=False):
                    return False
            await self._subscribe_events()
            return True
        except (ImportError, ThinQAPIException, aiohttp.ClientError, OSError, RuntimeError) as error:
            _LOGGER.warning("Live updates unavailable: %s", error)
            return False

    async def stop(self) -> None:
        connection = getattr(self._client, "_mqtt_connection", None)
        if connection is not None:
            # Disconnect only: keeping the registration lets the cached certificate work next time.
            await asyncio.get_running_loop().run_in_executor(None, lambda: connection.disconnect().result(5))
        self._client = None

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def _connect(self, use_cache: bool) -> bool:
        if not (use_cache and self._load_credentials()):
            if not await self._client.async_prepare_mqtt():
                return False
            self._save_credentials()
        await self._client.async_connect_mqtt()
        if not self._client.is_connected:
            self._clear_credentials()
            return False
        return True

    async def _subscribe_events(self) -> None:
        marker = self.store_dir / f"subscribed-{self.device_id}"
        if marker.exists():
            return
        try:
            await self.api.async_post_event_subscribe(self.device_id)
        except ThinQAPIException as error:
            if "already" not in str(error.message).lower() and str(error.code) not in {"1207", "1209"}:
                raise
        marker.touch()

    def _on_message(self, topic=None, payload=b"", **_kwargs) -> None:
        # Called on an AWS IoT thread; hand the report to the asyncio loop.
        report = parse_message(payload, self.device_id)
        if report is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_report, report)

    def _connection_changed(self, connected: bool) -> None:
        if self.on_connection_change and self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_connection_change, connected)

    # ---------- cached certificate ----------

    def _load_credentials(self) -> bool:
        try:
            cert, key, ca, topic = (
                (self.store_dir / name).read_bytes() for name in _FILES
            )
        except FileNotFoundError:
            return False
        self._client.bytes_certificate = cert
        self._client.bytes_private_key = key
        self._client.bytes_root_ca = ca
        self._client.topic_subscription = topic.decode()
        return True

    def _save_credentials(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.store_dir, 0o700)
        values = (
            self._client.bytes_certificate,
            self._client.bytes_private_key,
            self._client.bytes_root_ca,
            self._client.topic_subscription.encode(),
        )
        for name, value in zip(_FILES, values):
            path = self.store_dir / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(value)

    def _clear_credentials(self) -> None:
        for name in _FILES:
            (self.store_dir / name).unlink(missing_ok=True)
