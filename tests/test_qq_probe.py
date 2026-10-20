import unittest
from unittest.mock import Mock

from qq_probe import NapCat, image_type, verify_added


class ProbeTests(unittest.TestCase):
    def test_riff_is_not_always_webp(self):
        self.assertIsNone(image_type(b"RIFF0000WAVEdata"))
        self.assertEqual(image_type(b"RIFF0000WEBPdata"), "webp")

    def test_remote_endpoints_rejected(self):
        for endpoint in ("https://example.com", "http://127.0.0.1.evil.com", "http://user:pass@localhost:3000"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                NapCat(endpoint)

    def test_add_requires_observation(self):
        client = Mock()
        client.faces.return_value = []
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            verify_added(client, "abcd", 2000, attempts=2, interval=0)
        self.assertEqual(client.faces.call_count, 2)

    def test_delayed_case_insensitive_confirmation(self):
        client = Mock()
        client.faces.side_effect = [[], [{"md5": "ABCD", "resId": "resource"}]]
        result = verify_added(client, "abcd", 2000, attempts=2, interval=0)
        self.assertEqual(result["resId"], "resource")

    def test_failed_api_not_treated_as_empty_list(self):
        client = NapCat("http://127.0.0.1:3000")
        response = Mock()
        response.read.return_value = b'{"status":"failed","retcode":1400,"data":null}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        client.opener = Mock()
        client.opener.open.return_value = response
        with self.assertRaisesRegex(RuntimeError, "did not return success"):
            client.faces(2000)


if __name__ == "__main__":
    unittest.main()
