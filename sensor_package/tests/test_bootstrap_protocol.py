import json
from pathlib import Path

import pytest

from sensor_package.tools.bootstrap.protocol import (
    HARDWARE_ID_PATTERN,
    ProtocolError,
    content_hash,
    decode_response,
    encode_request,
    validate_backup,
)


def test_request_is_bounded_and_correlated():
    line = encode_request("req-1", "provision_identity", node_id="MG24-0001")
    assert len(line) <= 768 and line.startswith(b"MG24BOOT1 ")
    response = (
        b'MG24BOOT1 {"type":"bootstrap_response","schema_version":1,'
        b'"request_id":"req-1","action":"provision_identity",'
        b'"status":"success","result":{}}\n'
    )
    assert decode_response(response, "req-1", "provision_identity")["status"] == "success"
    with pytest.raises(ProtocolError, match="correlation"):
        decode_response(response, "different", "provision_identity")


def test_unknown_action_and_oversize_rejected():
    with pytest.raises(ProtocolError):
        encode_request("x", "arbitrary_nvm_write")
    with pytest.raises(ProtocolError):
        encode_request("x", "restore_configuration", junk="x" * 800)


def test_backup_hash_validation():
    backup = {
        "backup_schema_version": 1,
        "created_at_utc": "2026-08-07T00:00:00Z",
        "source_node_id": "MG24-0001",
        "identity_included": False,
        "state": {},
    }
    backup["canonical_content_hash"] = content_hash(backup)
    validate_backup(backup)
    backup["state"] = {"tampered": True}
    with pytest.raises(ProtocolError, match="hash"):
        validate_backup(backup)


def test_bootstrap_fixtures_are_data_only():
    fixtures = Path(__file__).parents[2] / "shared_protocol/fixtures"
    for path in fixtures.glob("bootstrap_*.json"):
        assert isinstance(json.loads(path.read_text()), dict)


def test_bootstrap_schema_action_and_version_constraints_match_fixtures():
    root = Path(__file__).parents[2]
    schema = json.loads((root / "shared_protocol/schemas/bootstrap_request.schema.json").read_text())
    allowed = set(schema["properties"]["action"]["enum"])
    assert schema["properties"]["schema_version"]["const"] == 1
    fixtures = root / "shared_protocol/fixtures"
    assert json.loads((fixtures / "bootstrap_read_request.json").read_text())["action"] in allowed
    assert json.loads((fixtures / "bootstrap_unknown_action.json").read_text())["action"] not in allowed
    assert json.loads((fixtures / "bootstrap_unsupported_version.json").read_text())["schema_version"] != 1
    prepare = json.loads((fixtures / "bootstrap_reset_prepare.json").read_text())
    confirm = json.loads((fixtures / "bootstrap_reset_confirm.json").read_text())
    assert prepare["reset_protocol_version"] == confirm["reset_protocol_version"] == 2
    assert HARDWARE_ID_PATTERN.fullmatch(prepare["expected_hardware_id"])
    assert confirm["expected_hardware_id"] == prepare["expected_hardware_id"]
    assert len(confirm["operation_id"]) == len(confirm["challenge"]) == 32
