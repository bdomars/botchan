import unittest

from cryptography.fernet import Fernet
from pydantic import ValidationError

from botchan_api.app import ConfigWrite, config_etag, valid_snowflake
from botchan_api.security import Security


class SecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.security = Security("test-session-secret", Fernet.generate_key().decode())

    def test_oauth_state_is_signed_and_time_limited(self) -> None:
        signed = self.security.sign_oauth_state("state")
        self.assertTrue(self.security.verify_oauth_state(signed, "state"))
        self.assertFalse(self.security.verify_oauth_state(signed, "different"))
        self.assertFalse(self.security.verify_oauth_state("invalid", "state"))

    def test_discord_tokens_are_encrypted(self) -> None:
        encrypted = self.security.encrypt("secret-token")
        self.assertNotIn("secret-token", encrypted)
        self.assertEqual(self.security.decrypt(encrypted), "secret-token")


class APIContractTests(unittest.TestCase):
    def test_snowflakes_are_validated_as_decimal_strings(self) -> None:
        self.assertTrue(valid_snowflake("123456789012345678"))
        self.assertFalse(valid_snowflake("0"))
        self.assertFalse(valid_snowflake("123.0"))
        self.assertFalse(valid_snowflake("1" * 21))

    def test_etag_is_revision_specific(self) -> None:
        self.assertEqual(config_etag(4), '"guild-config-4"')

    def test_write_requires_at_least_one_unique_valid_pool(self) -> None:
        valid = ConfigWrite.model_validate(
            {
                "channel_pools": [
                    {
                        "base_name": " Other Games ",
                        "min_channels": 3,
                        "max_channels": 10,
                        "idle_seconds": 600,
                    }
                ]
            }
        )
        self.assertEqual(valid.channel_pools[0].base_name, "Other Games")

        with self.assertRaises(ValidationError):
            ConfigWrite.model_validate({"channel_pools": []})
        with self.assertRaises(ValidationError):
            ConfigWrite.model_validate(
                {"channel_pools": [{"base_name": "Same"}, {"base_name": "Same"}]}
            )


if __name__ == "__main__":
    unittest.main()
