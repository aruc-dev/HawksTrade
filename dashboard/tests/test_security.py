"""Tests for auth mode handling + Cloudflare JWT verification.

Avoids network: monkeypatches the JWKS client with a locally generated RSA key
so jwt.decode() works entirely offline.
"""
from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

try:
    from fastapi import HTTPException
    from dashboard import security
except ImportError as exc:
    raise unittest.SkipTest(
        "fastapi not installed; run pip install -r requirements-dashboard.txt"
    ) from exc

from dashboard import config as dash_config


def _local_mode():
    return patch.dict(
        os.environ,
        {"DASHBOARD_AUTH_MODE": "local"},
        clear=False,
    )


class AuthModeTests(unittest.TestCase):
    def test_unknown_mode_rejected(self):
        with patch.dict(os.environ, {"DASHBOARD_AUTH_MODE": "wide-open"}, clear=False):
            with self.assertRaises(ValueError):
                dash_config.get_auth_mode()

    def test_local_mode_returns_local_ssh_identity(self):
        import asyncio

        with _local_mode():
            # require_auth is an async function; run it with a fake Request.
            class FakeReq:
                state = type("S", (), {})()
                headers = {}
                cookies = {}

            email = asyncio.run(security.require_auth(FakeReq()))
        self.assertEqual(email, "local-ssh")
        self.assertEqual(FakeReq.state.identity, "local-ssh")

    def test_cloudflare_mode_uses_header_token(self):
        import asyncio

        class FakeReq:
            state = type("S", (), {})()
            headers = {"Cf-Access-Jwt-Assertion": "header-token"}
            cookies = {}

        env = {
            "DASHBOARD_AUTH_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "test.cloudflareaccess.com",
            "CF_ACCESS_AUD": "aud-abc",
            "DASHBOARD_ALLOWED_EMAILS": "arun@example.com",
        }
        with patch.dict(os.environ, env, clear=False), \
                patch.object(
                    security, "verify_cloudflare_jwt", return_value="arun@example.com"
                ) as verify:
            email = asyncio.run(security.require_auth(FakeReq()))
        verify.assert_called_once_with("header-token")
        self.assertEqual(email, "arun@example.com")
        self.assertEqual(FakeReq.state.identity, "arun@example.com")

    def test_cloudflare_mode_falls_back_to_cookie(self):
        import asyncio

        class FakeReq:
            state = type("S", (), {})()
            headers = {}
            cookies = {"CF_Authorization": "cookie-token"}

        env = {
            "DASHBOARD_AUTH_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "test.cloudflareaccess.com",
            "CF_ACCESS_AUD": "aud-abc",
            "DASHBOARD_ALLOWED_EMAILS": "arun@example.com",
        }
        with patch.dict(os.environ, env, clear=False), \
                patch.object(
                    security, "verify_cloudflare_jwt", return_value="arun@example.com"
                ) as verify:
            email = asyncio.run(security.require_auth(FakeReq()))
        verify.assert_called_once_with("cookie-token")
        self.assertEqual(email, "arun@example.com")
        self.assertEqual(FakeReq.state.identity, "arun@example.com")


class ProductionAssertionTests(unittest.TestCase):
    def test_cloudflare_requires_team_domain(self):
        env = {
            "DASHBOARD_AUTH_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "",
            "CF_ACCESS_AUD": "x",
            "DASHBOARD_ALLOWED_EMAILS": "a@b.com",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(RuntimeError):
                security.assert_production_auth_safe()

    def test_cloudflare_requires_audience(self):
        env = {
            "DASHBOARD_AUTH_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "x.cloudflareaccess.com",
            "CF_ACCESS_AUD": "",
            "DASHBOARD_ALLOWED_EMAILS": "a@b.com",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(RuntimeError):
                security.assert_production_auth_safe()

    def test_cloudflare_requires_allowlist(self):
        env = {
            "DASHBOARD_AUTH_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "x.cloudflareaccess.com",
            "CF_ACCESS_AUD": "aud",
            "DASHBOARD_ALLOWED_EMAILS": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(RuntimeError):
                security.assert_production_auth_safe()

    def test_local_mode_passes_assertion(self):
        with _local_mode():
            security.assert_production_auth_safe()  # no raise


class CloudflareJWTVerificationTests(unittest.TestCase):
    """Generate a local RSA key, sign a token, monkeypatch the JWKS client."""

    @classmethod
    def setUpClass(cls):
        try:
            import jwt  # noqa: F401
            from cryptography.hazmat.primitives import serialization  # noqa: F401
            from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pyjwt/cryptography not installed; skipping JWT tests")

    def _make_key_and_signer(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        class FakeSigningKey:
            def __init__(self, k):
                self.key = k

        class FakeJWKSClient:
            def __init__(self, *_, **__):
                pass

            def get_signing_key_from_jwt(self, token):
                return FakeSigningKey(key.public_key())

        return key, FakeJWKSClient

    def _env(self, **overrides):
        base = {
            "DASHBOARD_AUTH_MODE": "cloudflare",
            "CF_ACCESS_TEAM_DOMAIN": "test.cloudflareaccess.com",
            "CF_ACCESS_AUD": "aud-abc",
            "DASHBOARD_ALLOWED_EMAILS": "arun@example.com",
        }
        base.update(overrides)
        return base

    def test_valid_jwt_accepted(self):
        import jwt as pyjwt

        key, FakeJWKSClient = self._make_key_and_signer()
        now = int(time.time())
        token = pyjwt.encode(
            {
                "iss": "https://test.cloudflareaccess.com",
                "aud": "aud-abc",
                "email": "arun@example.com",
                "exp": now + 60,
                "iat": now,
            },
            key,
            algorithm="RS256",
        )
        with patch.dict(os.environ, self._env(), clear=False), \
                patch.object(security, "_JWKS_CLIENT", FakeJWKSClient()):
            email = security.verify_cloudflare_jwt(token)
        self.assertEqual(email, "arun@example.com")

    def test_missing_token_rejected(self):
        with patch.dict(os.environ, self._env(), clear=False):
            with self.assertRaises(HTTPException) as ctx:
                security.verify_cloudflare_jwt("")
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("missing Cloudflare Access token", ctx.exception.detail)

    def test_wrong_audience_rejected(self):
        import jwt as pyjwt

        key, FakeJWKSClient = self._make_key_and_signer()
        now = int(time.time())
        token = pyjwt.encode(
            {
                "iss": "https://test.cloudflareaccess.com",
                "aud": "WRONG_AUD",
                "email": "arun@example.com",
                "exp": now + 60,
                "iat": now,
            },
            key,
            algorithm="RS256",
        )
        with patch.dict(os.environ, self._env(), clear=False), \
                patch.object(security, "_JWKS_CLIENT", FakeJWKSClient()):
            with self.assertRaises(HTTPException) as ctx:
                security.verify_cloudflare_jwt(token)
            self.assertEqual(ctx.exception.status_code, 401)

    def test_email_not_in_allowlist_rejected(self):
        import jwt as pyjwt

        key, FakeJWKSClient = self._make_key_and_signer()
        now = int(time.time())
        token = pyjwt.encode(
            {
                "iss": "https://test.cloudflareaccess.com",
                "aud": "aud-abc",
                "email": "stranger@example.com",
                "exp": now + 60,
                "iat": now,
            },
            key,
            algorithm="RS256",
        )
        with patch.dict(os.environ, self._env(), clear=False), \
                patch.object(security, "_JWKS_CLIENT", FakeJWKSClient()):
            with self.assertRaises(HTTPException) as ctx:
                security.verify_cloudflare_jwt(token)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_missing_config_returns_500(self):
        with patch.dict(os.environ, self._env(CF_ACCESS_TEAM_DOMAIN=""), clear=False):
            with self.assertRaises(HTTPException) as ctx:
                security.verify_cloudflare_jwt("ignored")
            self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
