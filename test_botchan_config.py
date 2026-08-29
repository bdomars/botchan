import json
import tempfile
import unittest
from pathlib import Path

from botchan.config import load_runtime_config_file, parse_runtime_config_data


class ConfigTests(unittest.TestCase):
    def test_parse_runtime_config_data_supports_multiple_guilds_and_pools(self) -> None:
        config = parse_runtime_config_data(
            {
                "guilds": [
                    {
                        "guild_id": 111,
                        "channel_pools": [
                            {
                                "base_name": "Other Games",
                                "min_channels": 2,
                                "max_channels": 10,
                                "idle_seconds": 300,
                            },
                            {
                                "base_name": "Raid Rooms",
                                "min_channels": 1,
                                "max_channels": 4,
                                "idle_seconds": 60,
                            },
                        ],
                    },
                    {
                        "guild_id": 222,
                        "channel_pools": [{"base_name": "Side Quests"}],
                    },
                ],
            }
        )

        self.assertEqual(set(config.guilds), {111, 222})
        self.assertEqual(len(config.guilds[111].channel_pools), 2)
        self.assertEqual(config.guilds[222].channel_pools[0].base_name, "Side Quests")
        self.assertEqual(config.guilds[222].channel_pools[0].min_channels, 3)

    def test_parse_runtime_config_data_rejects_duplicate_pool_base_names_in_guild(
        self,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "duplicates another pool"):
            parse_runtime_config_data(
                {
                    "guilds": [
                        {
                            "guild_id": 111,
                            "channel_pools": [
                                {"base_name": "Other Games"},
                                {"base_name": "Other Games"},
                            ],
                        },
                    ],
                }
            )

    def test_parse_runtime_config_data_rejects_invalid_pool_bounds(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "max_channels"):
            parse_runtime_config_data(
                {
                    "guilds": [
                        {
                            "guild_id": 111,
                            "channel_pools": [
                                {
                                    "base_name": "Other Games",
                                    "min_channels": 5,
                                    "max_channels": 4,
                                },
                            ],
                        },
                    ],
                }
            )

    def test_parse_runtime_config_data_rejects_max_channels_less_than_one(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "max_channels"):
            parse_runtime_config_data(
                {
                    "guilds": [
                        {
                            "guild_id": 111,
                            "channel_pools": [
                                {"base_name": "Other Games", "max_channels": 0},
                            ],
                        },
                    ],
                }
            )

    def test_load_runtime_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "guilds": [
                            {
                                "guild_id": 111,
                                "channel_pools": [{"base_name": "Other Games"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            config = load_runtime_config_file(path)

        self.assertEqual(set(config.guilds), {111})


if __name__ == "__main__":
    unittest.main()
