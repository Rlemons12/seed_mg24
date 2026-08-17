import hashlib
import json
from pathlib import Path

import pytest

from gateway.app.ble.onboarding_identity import (
    DOMAIN,
    MAX_PAYLOAD_BYTES,
    OnboardingIdentityError,
    canonical_hardware_id,
    derive_onboarding_identity,
    parse_onboarding_payload,
)


def payload(**updates):
    value = {"schema_version": 1, "onboarding_identity": "7e6d1066299f5557d6bfee9bf6e454a8",
             "provisioning_state": "unprovisioned", "protocol_version": "1.0.0", "firmware_version": "0.1.0"}
    value.update(updates)
    return json.dumps(value).encode()


def test_identity_derivation_is_canonical_stable_domain_separated_and_128_bits():
    assert canonical_hardware_id("0X0123456789abcdef") == "0x0123456789ABCDEF"
    expected = "7e6d1066299f5557d6bfee9bf6e454a8"
    assert derive_onboarding_identity("0x0123456789ABCDEF") == expected
    assert derive_onboarding_identity("0X0123456789abcdef") == expected
    assert len(bytes.fromhex(expected)) == 16
    assert expected != hashlib.sha256(b"0x0123456789ABCDEF").hexdigest()[:32]
    assert DOMAIN == b"MG24-ONBOARDING-V1"


@pytest.mark.parametrize("bad", ["0x123", "0xGGGGGGGGGGGGGGGG", "0x0123456789ABCDEFF"])
def test_hardware_id_normalization_rejects_noncanonical_shape(bad):
    with pytest.raises(OnboardingIdentityError):
        canonical_hardware_id(bad)


def test_payload_is_bounded_and_accepts_unprovisioned_or_recovery_identity():
    assert len(payload()) <= MAX_PAYLOAD_BYTES
    assert parse_onboarding_payload(payload())["provisioning_state"] == "unprovisioned"
    assert parse_onboarding_payload(payload(provisioning_state="recovery"))["onboarding_identity"]


def test_provisioned_payload_must_not_expose_identity():
    safe = json.dumps({"schema_version": 1, "provisioning_state": "provisioned",
                       "protocol_version": "1.0.0"}).encode()
    assert "onboarding_identity" not in parse_onboarding_payload(safe)
    with pytest.raises(OnboardingIdentityError, match="exposed"):
        parse_onboarding_payload(payload(provisioning_state="provisioned"))


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"schema_version":2}',
                                  b'{"schema_version":1,"provisioning_state":"unprovisioned","onboarding_identity":"bad"}'])
def test_malformed_and_unsupported_payloads_fail_closed(raw):
    with pytest.raises(OnboardingIdentityError):
        parse_onboarding_payload(raw)


def test_oversized_payload_fails_closed():
    with pytest.raises(OnboardingIdentityError, match="oversized"):
        parse_onboarding_payload(b"x" * (MAX_PAYLOAD_BYTES + 1))


def test_protocol_fixtures_validate_and_provisioned_fixture_has_no_identity():
    root = Path(__file__).parents[2] / "shared_protocol"
    schema = json.loads((root / "schemas/onboarding_identity.schema.json").read_text(encoding="utf-8"))
    unprovisioned = json.loads((root / "fixtures/onboarding_identity_unprovisioned.json").read_text(encoding="utf-8"))
    provisioned = json.loads((root / "fixtures/onboarding_identity_provisioned.json").read_text(encoding="utf-8"))
    assert schema["oneOf"][0]["properties"]["onboarding_identity"]["pattern"] == "^[0-9a-f]{32}$"
    assert parse_onboarding_payload(json.dumps(unprovisioned).encode()) == unprovisioned
    assert parse_onboarding_payload(json.dumps(provisioned).encode()) == provisioned
    assert "onboarding_identity" not in provisioned
