"""
Regression tests for ``services.dodo_service.DodoPaymentsService.verify_webhook_signature``.

The previous implementation logged the full HMAC signature at INFO level
inside ``verify_webhook_signature``. That's two problems:

1. INFO is the default level in production; full HMAC outputs and
   received signatures then land in shipped log streams.
2. Multiple full sigs over known payloads are a forgery primitive
   (an attacker who can guess the verifier output can replay/forge).

This test pins the post-fix behavior:
- At INFO/ WARNING, no record contains the full expected or received signature.
- DEBUG-level logs may surface a length + first-8-chars prefix only.
"""
import logging
import os

import pytest

# Provide a valid whsec_ secret before settings reads it.
os.environ.setdefault("WEBHOOK_SIGNING_SECRET", "whsec_dGVzdF9zZWNyZXRfa2V5MTIzNDU2Nzg=")


def _build_service():
    """Build a DodoPaymentsService without invoking the live SDK.

    We monkeypatch the SDK call so the test doesn't need real credentials.
    """
    from services import dodo_service as svc
    original_client = svc.DodoPayments
    try:
        svc.DodoPayments = lambda **kw: object()
        service = svc.DodoPaymentsService()
        return service
    finally:
        svc.DodoPayments = original_client


@pytest.mark.unit
class TestWebhookSignatureLogging:
    """Verify HMAC material isn't logged at INFO; prefix-only at DEBUG."""

    PAYLOAD = "msg_2xyz.test_timestamp.{\"event\":\"payment.succeeded\"}"

    def _sign(self, service, payload):
        import base64
        import hmac
        import hashlib

        secret_bytes = service.webhook_secret.encode()
        if service.webhook_secret.startswith("whsec_"):
            secret_bytes = base64.b64decode(service.webhook_secret[6:])
        return base64.b64encode(
            hmac.new(secret_bytes, payload.encode(), hashlib.sha256).digest()
        ).decode()

    def test_no_full_signature_at_info_level(self, caplog):
        service = _build_service()
        valid = self._sign(service, self.PAYLOAD)

        # Info-level operation is what production runs at by default.
        with caplog.at_level(logging.INFO, logger="services.dodo_service"):
            service.verify_webhook_signature(self.PAYLOAD, valid)

        # Combine all records and verify no full signature leaked.
        joined = "\n".join(
            r.getMessage() + " " + str(getattr(r, "extra_fields", {}))
            for r in caplog.records
            if r.levelno >= logging.INFO
        )
        assert valid not in joined, (
            "Full HMAC signature leaked at INFO level — security regression."
        )

    def test_no_full_signature_at_warning_level(self, caplog):
        service = _build_service()
        bogus = "totally-not-a-real-signature"

        with caplog.at_level(logging.WARNING, logger="services.dodo_service"):
            service.verify_webhook_signature(self.PAYLOAD, bogus)

        joined = "\n".join(
            r.getMessage() + " " + str(getattr(r, "extra_fields", {}))
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )
        # Even on bad sig, only prefixes/lengths should appear.
        for sig in (bogus, "totally-not-a-real-signature"):
            assert sig not in joined

    def test_debug_only_emits_prefix_redacted_line(self, caplog):
        service = _build_service()
        valid = self._sign(service, self.PAYLOAD)

        with caplog.at_level(logging.DEBUG, logger="services.dodo_service"):
            service.verify_webhook_signature(self.PAYLOAD, valid)

        debug_lines = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert debug_lines, "DEBUG-level record should have been emitted"
        joined = "\n".join(
            r.getMessage() + " " + str(getattr(r, "extra_fields", {}))
            for r in debug_lines
        )
        # Full sig should NOT appear; a short prefix SHOULD appear.
        assert valid not in joined
        assert valid[:8] in joined
