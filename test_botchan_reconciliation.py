import unittest
from dataclasses import dataclass, field

from botchan.client import ManagedPool
from botchan.config import ChannelPoolSpec
from botchan.reconciliation import (
    SYNCED_ATTRIBUTES,
    base_channel,
    channels_needing_sync,
    desired_channel_count,
    parse_channel_number,
    plan_reconcile,
    refresh_empty_timers,
    settings_fingerprint,
)

SPEC = ChannelPoolSpec(
    base_name="Other Games",
    min_channels=3,
    max_channels=10,
    idle_seconds=600,
)


@dataclass
class FakeChannel:
    id: int
    name: str
    members: list[object] = field(default_factory=list)
    category_id: int | None = None
    overwrites: dict[object, object] = field(default_factory=dict)
    bitrate: int = 64000
    user_limit: int = 0
    rtc_region: str | None = None
    video_quality_mode: object = 1


class OtherGamesChannelTests(unittest.TestCase):
    def test_parse_channel_names(self) -> None:
        self.assertEqual(parse_channel_number("Other Games", SPEC), 1)
        self.assertEqual(parse_channel_number("Other Games #2", SPEC), 2)
        self.assertEqual(parse_channel_number("Other Games #14", SPEC), 14)
        self.assertIsNone(parse_channel_number("Other Games #1", SPEC))
        self.assertIsNone(parse_channel_number("Other Game", SPEC))
        self.assertIsNone(parse_channel_number("Other Games 2", SPEC))

    def test_parse_channel_names_with_custom_base_name(self) -> None:
        spec = ChannelPoolSpec(
            base_name="Side Quests",
            min_channels=2,
            max_channels=5,
            idle_seconds=300,
        )

        self.assertEqual(parse_channel_number("Side Quests", spec), 1)
        self.assertEqual(parse_channel_number("Side Quests #2", spec), 2)
        self.assertIsNone(parse_channel_number("Other Games", spec))

    def test_desired_count_keeps_minimum_when_empty(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", []),
            FakeChannel(102, "Other Games #2", []),
        ]

        self.assertEqual(desired_channel_count(channels, SPEC), 3)

    def test_desired_count_adds_one_spare_channel(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", [object()]),
            FakeChannel(102, "Other Games #2", [object()]),
            FakeChannel(103, "Other Games #3", []),
        ]

        self.assertEqual(desired_channel_count(channels, SPEC), 3)

    def test_desired_count_is_capped_at_max(self) -> None:
        spec = ChannelPoolSpec(
            base_name="Other Games",
            min_channels=3,
            max_channels=4,
            idle_seconds=600,
        )
        channels = [
            FakeChannel(101, "Other Games", [object()]),
            FakeChannel(102, "Other Games #2", [object()]),
            FakeChannel(103, "Other Games #3", [object()]),
            FakeChannel(104, "Other Games #4", [object()]),
        ]

        self.assertEqual(desired_channel_count(channels, spec), 4)

    def test_creates_missing_minimum_channels(self) -> None:
        channels = [FakeChannel(101, "Other Games", [])]

        plan = plan_reconcile(channels, {}, 0.0, SPEC)

        self.assertEqual(plan.create_numbers, [2, 3])
        self.assertEqual(plan.delete_channel_ids, [])
        self.assertIsNone(plan.blocked_reason)

    def test_creates_four_when_first_three_are_occupied(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", [object()]),
            FakeChannel(102, "Other Games #2", [object()]),
            FakeChannel(103, "Other Games #3", [object()]),
        ]

        plan = plan_reconcile(channels, {}, 0.0, SPEC)

        self.assertEqual(plan.create_numbers, [4])

    def test_creates_lowest_missing_channel_number(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", [object()]),
            FakeChannel(103, "Other Games #3", [object()]),
        ]

        plan = plan_reconcile(channels, {}, 0.0, SPEC)

        self.assertEqual(plan.create_numbers, [2])

    def test_does_not_create_when_empty_spare_exists(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", [object()]),
            FakeChannel(102, "Other Games #2", [object()]),
            FakeChannel(103, "Other Games #3", []),
        ]

        plan = plan_reconcile(channels, {}, 0.0, SPEC)

        self.assertEqual(plan.create_numbers, [])
        self.assertEqual(plan.delete_channel_ids, [])

    def test_deletes_only_channels_above_min_after_idle_timeout(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", []),
            FakeChannel(102, "Other Games #2", []),
            FakeChannel(103, "Other Games #3", []),
            FakeChannel(104, "Other Games #4", []),
            FakeChannel(105, "Other Games #5", []),
        ]
        empty_since = {104: 100.0, 105: 100.0}

        plan = plan_reconcile(channels, empty_since, 701.0, SPEC)

        self.assertEqual(plan.delete_channel_ids, [105, 104])

    def test_never_deletes_channels_at_or_below_min(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", []),
            FakeChannel(102, "Other Games #2", []),
            FakeChannel(103, "Other Games #3", []),
        ]
        empty_since = {101: 0.0, 102: 0.0, 103: 0.0}

        plan = plan_reconcile(channels, empty_since, 9999.0, SPEC)

        self.assertEqual(plan.delete_channel_ids, [])

    def test_does_not_delete_protected_min_channel_to_satisfy_excess_count(
        self,
    ) -> None:
        channels = [
            FakeChannel(101, "Other Games", []),
            FakeChannel(102, "Other Games #2", []),
            FakeChannel(103, "Other Games #3", []),
            FakeChannel(104, "Other Games #4", [object()]),
        ]
        empty_since = {101: 0.0, 102: 0.0, 103: 0.0}

        plan = plan_reconcile(channels, empty_since, 9999.0, SPEC)

        self.assertEqual(plan.delete_channel_ids, [])

    def test_does_not_delete_occupied_excess_channels(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", []),
            FakeChannel(102, "Other Games #2", []),
            FakeChannel(103, "Other Games #3", []),
            FakeChannel(104, "Other Games #4", [object()]),
        ]
        empty_since = {104: 0.0}

        plan = plan_reconcile(channels, empty_since, 9999.0, SPEC)

        self.assertEqual(plan.delete_channel_ids, [])

    def test_does_not_delete_before_idle_timeout(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", []),
            FakeChannel(102, "Other Games #2", []),
            FakeChannel(103, "Other Games #3", []),
            FakeChannel(104, "Other Games #4", []),
        ]
        empty_since = {104: 100.0}

        plan = plan_reconcile(channels, empty_since, 699.0, SPEC)

        self.assertEqual(plan.delete_channel_ids, [])

    def test_duplicate_channel_numbers_block_changes(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", [object()]),
            FakeChannel(104, "Other Games #4", []),
            FakeChannel(204, "Other Games #4", []),
        ]

        plan = plan_reconcile(channels, {104: 0.0, 204: 0.0}, 9999.0, SPEC)

        self.assertEqual(plan.create_numbers, [])
        self.assertEqual(plan.delete_channel_ids, [])
        self.assertEqual(plan.blocked_reason, "duplicate channel numbers: 4")


class ManagedPoolTests(unittest.TestCase):
    def test_empty_timers_are_isolated_per_pool(self) -> None:
        games_pool = ManagedPool(spec=SPEC)
        raid_pool = ManagedPool(
            spec=ChannelPoolSpec(
                base_name="Raid Rooms",
                min_channels=1,
                max_channels=3,
                idle_seconds=60,
            )
        )
        channels = [
            FakeChannel(101, "Other Games", []),
            FakeChannel(201, "Raid Rooms", []),
        ]

        refresh_empty_timers(
            channels, games_pool.empty_since_by_channel_id, 10.0, games_pool.spec
        )
        refresh_empty_timers(
            channels, raid_pool.empty_since_by_channel_id, 20.0, raid_pool.spec
        )

        self.assertEqual(games_pool.empty_since_by_channel_id, {101: 10.0})
        self.assertEqual(raid_pool.empty_since_by_channel_id, {201: 20.0})

    def test_empty_timer_refresh_removes_stale_pool_channels(self) -> None:
        empty_since = {101: 10.0, 999: 5.0}
        channels = [
            FakeChannel(101, "Other Games", [object()]),
            FakeChannel(201, "Raid Rooms", []),
        ]

        refresh_empty_timers(channels, empty_since, 20.0, SPEC)

        self.assertEqual(empty_since, {})


class BaseChannelTests(unittest.TestCase):
    def test_prefers_channel_number_one(self) -> None:
        first = FakeChannel(101, "Other Games")
        channels = [FakeChannel(103, "Other Games #3"), first]

        self.assertIs(base_channel(channels, SPEC), first)

    def test_falls_back_to_lowest_numbered_channel(self) -> None:
        second = FakeChannel(102, "Other Games #2")
        channels = [FakeChannel(104, "Other Games #4"), second]

        self.assertIs(base_channel(channels, SPEC), second)

    def test_ignores_unrelated_channels(self) -> None:
        channels = [FakeChannel(301, "Raid Rooms"), FakeChannel(302, "General")]

        self.assertIsNone(base_channel(channels, SPEC))

    def test_returns_none_without_channels(self) -> None:
        self.assertIsNone(base_channel([], SPEC))


class SettingsSyncTests(unittest.TestCase):
    def test_empty_pool_needs_no_sync(self) -> None:
        self.assertEqual(channels_needing_sync([], SPEC), [])

    def test_matching_channels_need_no_sync(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", category_id=7),
            FakeChannel(102, "Other Games #2", category_id=7),
        ]

        self.assertEqual(channels_needing_sync(channels, SPEC), [])

    def test_base_channel_is_never_its_own_sync_target(self) -> None:
        channels = [FakeChannel(101, "Other Games", category_id=7)]

        self.assertEqual(channels_needing_sync(channels, SPEC), [])

    def test_drifted_channels_are_returned_in_number_order(self) -> None:
        base = FakeChannel(101, "Other Games", bitrate=96000)
        third = FakeChannel(103, "Other Games #3", bitrate=64000)
        second = FakeChannel(102, "Other Games #2", bitrate=64000)
        matching = FakeChannel(104, "Other Games #4", bitrate=96000)

        result = channels_needing_sync([third, matching, base, second], SPEC)

        self.assertEqual([channel.id for channel in result], [102, 103])

    def test_channels_outside_the_pool_are_left_alone(self) -> None:
        channels = [
            FakeChannel(101, "Other Games", bitrate=96000),
            FakeChannel(201, "Raid Rooms", bitrate=64000),
        ]

        self.assertEqual(channels_needing_sync(channels, SPEC), [])

    def test_each_synced_attribute_triggers_a_sync(self) -> None:
        drifted = {
            "category_id": 9,
            "overwrites": {"role": object()},
            "bitrate": 96000,
            "user_limit": 4,
            "rtc_region": "rotterdam",
            "video_quality_mode": 2,
        }
        self.assertEqual(set(drifted), set(SYNCED_ATTRIBUTES))

        for attribute, value in drifted.items():
            with self.subTest(attribute=attribute):
                base = FakeChannel(101, "Other Games")
                other = FakeChannel(102, "Other Games #2")
                setattr(other, attribute, value)

                result = channels_needing_sync([base, other], SPEC)

                self.assertEqual([channel.id for channel in result], [102])

    def test_fingerprint_is_stable_for_identical_settings(self) -> None:
        first = FakeChannel(101, "Other Games", category_id=7, user_limit=4)
        second = FakeChannel(102, "Other Games #2", category_id=7, user_limit=4)

        self.assertEqual(settings_fingerprint(first), settings_fingerprint(second))

    def test_fingerprint_ignores_occupancy(self) -> None:
        empty = FakeChannel(101, "Other Games")
        occupied = FakeChannel(102, "Other Games #2", [object()])

        self.assertEqual(settings_fingerprint(empty), settings_fingerprint(occupied))


class SeedingTests(unittest.TestCase):
    def test_empty_pool_plans_every_channel_up_to_the_minimum(self) -> None:
        plan = plan_reconcile([], {}, 0.0, SPEC)

        self.assertEqual(plan.create_numbers, [1, 2, 3])
        self.assertIsNone(plan.blocked_reason)


if __name__ == "__main__":
    unittest.main()
