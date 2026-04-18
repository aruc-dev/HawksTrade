import json
import unittest
from types import SimpleNamespace

from alpaca.common.exceptions import APIError

from core.alpaca_errors import (
    CATEGORY_AUTH,
    CATEGORY_BROKER_REJECTION,
    CATEGORY_NOT_FOUND,
    CATEGORY_RATE_LIMIT,
    CATEGORY_SERVER,
    CATEGORY_TIMEOUT,
    call_alpaca,
    classify_alpaca_error,
)


class AlpacaErrorTests(unittest.TestCase):
    def _api_error(self, status_code, message):
        error = json.dumps({"code": status_code, "message": message})
        http_error = SimpleNamespace(
            response=SimpleNamespace(status_code=status_code),
            request=SimpleNamespace(),
        )
        return APIError(error, http_error)

    def test_classifies_auth_as_non_retryable(self):
        info = classify_alpaca_error(self._api_error(401, "unauthorized."))

        self.assertEqual(info.category, CATEGORY_AUTH)
        self.assertFalse(info.retryable)
        self.assertEqual(info.status_code, 401)

    def test_classifies_not_found_as_non_retryable(self):
        info = classify_alpaca_error(self._api_error(404, "position does not exist"))

        self.assertEqual(info.category, CATEGORY_NOT_FOUND)
        self.assertFalse(info.retryable)

    def test_classifies_rate_limit_as_retryable(self):
        info = classify_alpaca_error(self._api_error(429, "too many requests"))

        self.assertEqual(info.category, CATEGORY_RATE_LIMIT)
        self.assertTrue(info.retryable)

    def test_classifies_server_error_as_retryable(self):
        info = classify_alpaca_error(self._api_error(503, "service unavailable"))

        self.assertEqual(info.category, CATEGORY_SERVER)
        self.assertTrue(info.retryable)

    def test_classifies_timeout_as_retryable(self):
        info = classify_alpaca_error(TimeoutError("network timeout"))

        self.assertEqual(info.category, CATEGORY_TIMEOUT)
        self.assertTrue(info.retryable)

    def test_classifies_http_timeout_as_retryable_timeout(self):
        info = classify_alpaca_error(self._api_error(408, "request timeout"))

        self.assertEqual(info.category, CATEGORY_TIMEOUT)
        self.assertTrue(info.retryable)

    def test_classifies_other_4xx_as_broker_rejection(self):
        info = classify_alpaca_error(self._api_error(422, "insufficient buying power"))

        self.assertEqual(info.category, CATEGORY_BROKER_REJECTION)
        self.assertFalse(info.retryable)

    def test_call_alpaca_retries_retryable_errors(self):
        calls = []

        def flaky_call():
            calls.append("call")
            if len(calls) < 3:
                raise self._api_error(500, "temporary outage")
            return "ok"

        result = call_alpaca("test.retry", flaky_call, sleep_fn=lambda _: None)

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)

    def test_call_alpaca_does_not_retry_auth_errors(self):
        calls = []

        def auth_failure():
            calls.append("call")
            raise self._api_error(403, "forbidden")

        with self.assertRaises(APIError):
            call_alpaca("test.auth", auth_failure, sleep_fn=lambda _: None)

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
