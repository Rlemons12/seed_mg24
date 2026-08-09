import hashlib
import json
import re

DOMAIN = b"MG24-ONBOARDING-V1"
HARDWARE_ID_PATTERN = re.compile(r"^0x[0-9A-F]{16}$")
IDENTITY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MAX_PAYLOAD_BYTES = 192


class OnboardingIdentityError(ValueError):
    pass


def canonical_hardware_id(value: str) -> str:
    if not isinstance(value, str):
        raise OnboardingIdentityError("hardware ID must be a string")
    normalized = "0x" + value.removeprefix("0x").removeprefix("0X").upper()
    if not HARDWARE_ID_PATTERN.fullmatch(normalized):
        raise OnboardingIdentityError("hardware ID must contain exactly 16 hexadecimal digits")
    return normalized


def derive_onboarding_identity(hardware_id: str) -> str:
    canonical = canonical_hardware_id(hardware_id)
    return hashlib.sha256(DOMAIN + canonical.encode("ascii")).hexdigest()[:32]


def parse_onboarding_payload(raw: bytes) -> dict:
    if not raw or len(raw) > MAX_PAYLOAD_BYTES:
        raise OnboardingIdentityError("onboarding identity payload is empty or oversized")
    try:
        payload = json.loads(raw.rstrip(b"\x00").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OnboardingIdentityError("onboarding identity payload is malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise OnboardingIdentityError("onboarding identity schema is unsupported")
    state = payload.get("provisioning_state")
    if state == "provisioned":
        if "onboarding_identity" in payload:
            raise OnboardingIdentityError("provisioned response exposed a bootstrap identity")
        return payload
    if state not in {"unprovisioned", "recovery"}:
        raise OnboardingIdentityError("onboarding identity is unavailable")
    identity = payload.get("onboarding_identity")
    if not isinstance(identity, str) or not IDENTITY_PATTERN.fullmatch(identity):
        raise OnboardingIdentityError("onboarding identity encoding is invalid")
    if not isinstance(payload.get("protocol_version"), str) or not isinstance(payload.get("firmware_version"), str):
        raise OnboardingIdentityError("onboarding compatibility versions are missing")
    return payload
