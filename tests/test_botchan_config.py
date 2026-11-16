import unittest

from pydantic import ValidationError

from botchan.config import MIN_IDLE_SECONDS, GuildConfig


class GuildConfigTests(unittest.TestCase):
    def test_supports_multiple_pools_with_defaults(self) -> None:
        spec = GuildConfig.model_validate(
            {
                "guild_id": 111,
                "channel_pools": [
                    {
                        "base_name": "Other Games",
                        "min_channels": 2,
                        "max_channels": 10,
                        "idle_seconds": 300,
                    },
                    {"base_name": "Side Quests"},
                ],
            }
        ).to_spec()

        self.assertEqual(spec.guild_id, 111)
        self.assertEqual(len(spec.channel_pools), 2)
        self.assertEqual(spec.channel_pools[1].base_name, "Side Quests")
        self.assertEqual(spec.channel_pools[1].min_channels, 3)

    def test_idle_seconds_below_floor_is_raised_to_it(self) -> None:
        spec = GuildConfig.model_validate(
            {"guild_id": 111, "channel_pools": [{"base_name": "Other Games", "idle_seconds": 5}]}
        ).to_spec()

        self.assertEqual(spec.channel_pools[0].idle_seconds, MIN_IDLE_SECONDS)

    def test_rejects_duplicate_pool_base_names(self) -> None:
        with self.assertRaisesRegex(ValidationError, "duplicates another pool"):
            GuildConfig.model_validate(
                {
                    "guild_id": 111,
                    "channel_pools": [{"base_name": "Other Games"}, {"base_name": "Other Games"}],
                }
            )

    def test_rejects_invalid_pool_bounds(self) -> None:
        with self.assertRaisesRegex(ValidationError, "max_channels"):
            GuildConfig.model_validate(
                {
                    "guild_id": 111,
                    "channel_pools": [
                        {"base_name": "Other Games", "min_channels": 5, "max_channels": 4}
                    ],
                }
            )

    def test_rejects_max_channels_less_than_one(self) -> None:
        with self.assertRaisesRegex(ValidationError, "max_channels"):
            GuildConfig.model_validate(
                {
                    "guild_id": 111,
                    "channel_pools": [{"base_name": "Other Games", "max_channels": 0}],
                }
            )


if __name__ == "__main__":
    unittest.main()
