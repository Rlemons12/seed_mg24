from gateway.app.ble.scanner import BleScannerService


def test_compatibility_uses_service_uuid():
    compatible, reason = BleScannerService.classify("anything", ["0100004D-4724-2480-2D4D-47240024BEEF"])
    assert compatible and "service" in reason


def test_legacy_name_alone_is_not_compatible():
    compatible, reason = BleScannerService.classify("XIAO-MG24-Sense", [])
    assert not compatible and "not confirmed" in reason
