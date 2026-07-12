"""Minimal Agora AccessToken2 (v007) RTC token generator.

Based on Agora's official AgoraDynamicKey Python3 implementation.
"""

from __future__ import annotations

import base64
import hmac
import secrets
import struct
import time
import zlib
from hashlib import sha256

Role_Publisher = 1


def _u16(value: int) -> bytes:
    return struct.pack("<H", int(value))


def _u32(value: int) -> bytes:
    return struct.pack("<I", int(value))


def _string(value: bytes | str) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return _u16(len(data)) + data


class ServiceRtc:
    JOIN_CHANNEL = 1
    PUBLISH_AUDIO = 2
    PUBLISH_VIDEO = 3
    PUBLISH_DATA = 4

    def __init__(self, channel: str, uid: int) -> None:
        self.channel = channel.encode("utf-8")
        self.uid = str(uid).encode("utf-8") if uid else b""
        self.privileges: dict[int, int] = {}

    def add_privilege(self, privilege: int, expires_in: int) -> None:
        self.privileges[privilege] = expires_in

    def pack(self) -> bytes:
        privileges = b"".join(_u16(key) + _u32(value) for key, value in sorted(self.privileges.items()))
        return _u16(1) + _u16(len(self.privileges)) + privileges + _string(self.channel) + _string(self.uid)


class RtcTokenBuilder:
    @staticmethod
    def build_token_with_uid(
        app_id: str,
        app_certificate: str,
        channel_name: str,
        uid: int,
        role: int,
        token_expire: int,
        privilege_expire: int = 0,
    ) -> str:
        now = int(time.time())
        token_lifetime = token_expire
        privilege_lifetime = privilege_expire or token_expire
        service = ServiceRtc(channel_name, uid)
        service.add_privilege(ServiceRtc.JOIN_CHANNEL, privilege_lifetime)
        if role == Role_Publisher:
            service.add_privilege(ServiceRtc.PUBLISH_AUDIO, privilege_lifetime)
            service.add_privilege(ServiceRtc.PUBLISH_VIDEO, privilege_lifetime)
            service.add_privilege(ServiceRtc.PUBLISH_DATA, privilege_lifetime)

        salt = secrets.SystemRandom().randint(1, 99_999_999)
        signing_key = hmac.new(_u32(now), app_certificate.encode("utf-8"), sha256).digest()
        signing_key = hmac.new(_u32(salt), signing_key, sha256).digest()
        signing_info = (
            _string(app_id)
            + _u32(now)
            + _u32(token_lifetime)
            + _u32(salt)
            + _u16(1)
            + service.pack()
        )
        signature = hmac.new(signing_key, signing_info, sha256).digest()
        return "007" + base64.b64encode(zlib.compress(_string(signature) + signing_info)).decode("utf-8")
