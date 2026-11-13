from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from botchan.config import MAX_CHANNEL_NAME_LENGTH, ChannelPoolSpec

# Channel settings propagated from a pool's base channel to the rest of the
# pool. Kept in one place so the diff and the Discord edit cannot drift apart.
SYNCED_ATTRIBUTES = (
    "category_id",
    "overwrites",
    "bitrate",
    "user_limit",
    "rtc_region",
    "video_quality_mode",
)


class ManagedVoiceChannel(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def members(self) -> Sequence[object]: ...


class SyncableVoiceChannel(ManagedVoiceChannel, Protocol):
    @property
    def category_id(self) -> int | None: ...

    # Only ever compared for equality, never indexed, so the exact mapping
    # type does not matter here and pinning it would fight Mapping's
    # invariant key type against discord.py's concrete dict.
    @property
    def overwrites(self) -> object: ...

    @property
    def bitrate(self) -> int: ...

    @property
    def user_limit(self) -> int: ...

    @property
    def rtc_region(self) -> str | None: ...

    @property
    def video_quality_mode(self) -> object: ...


@dataclass(frozen=True)
class ReconcilePlan:
    create_numbers: list[int] = field(default_factory=list)
    delete_channel_ids: list[int] = field(default_factory=list)
    blocked_reason: str | None = None


def parse_channel_number(name: str, spec: ChannelPoolSpec) -> int | None:
    match = spec.channel_re.fullmatch(name)
    if not match:
        return None
    number = match.group("number")
    if number is None:
        return 1
    parsed_number = int(number)
    return parsed_number if parsed_number > 1 else None


def channel_is_occupied(channel: ManagedVoiceChannel) -> bool:
    return len(channel.members) > 0


def canonical_managed_channels[ChannelT: ManagedVoiceChannel](
    channels: Iterable[ChannelT], spec: ChannelPoolSpec
) -> dict[int, ChannelT]:
    managed: dict[int, ChannelT] = {}
    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is None:
            continue
        if number not in managed:
            managed[number] = channel
    return managed


def duplicate_extra_numbers(
    channels: Iterable[ManagedVoiceChannel], spec: ChannelPoolSpec
) -> set[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is None:
            continue
        if number in seen:
            duplicates.add(number)
        seen.add(number)
    return duplicates


def desired_channel_count(channels: Iterable[ManagedVoiceChannel], spec: ChannelPoolSpec) -> int:
    occupied_count = sum(1 for channel in channels if channel_is_occupied(channel))
    return min(max(occupied_count + 1, spec.min_channels), spec.max_channels)


def plan_reconcile(
    channels: Iterable[ManagedVoiceChannel],
    empty_since_by_channel_id: dict[int, float],
    now: float,
    spec: ChannelPoolSpec,
) -> ReconcilePlan:
    channel_list = list(channels)
    duplicate_numbers = duplicate_extra_numbers(channel_list, spec)
    if duplicate_numbers:
        duplicates = ", ".join(str(number) for number in sorted(duplicate_numbers))
        return ReconcilePlan(blocked_reason=f"duplicate channel numbers: {duplicates}")

    managed = canonical_managed_channels(channel_list, spec)
    desired_count = desired_channel_count(managed.values(), spec)
    current_count = len(managed)

    if current_count < desired_count:
        missing_numbers = [
            number for number in range(1, desired_count + 1) if number not in managed
        ]
        next_number = max(managed, default=0) + 1
        while len(missing_numbers) < desired_count - current_count:
            missing_numbers.append(next_number)
            next_number += 1
        return ReconcilePlan(create_numbers=missing_numbers[: desired_count - current_count])

    if current_count <= desired_count:
        return ReconcilePlan()

    excess_count = current_count - desired_count
    delete_channel_ids: list[int] = []
    for number, channel in sorted(managed.items(), reverse=True):
        if len(delete_channel_ids) >= excess_count:
            break
        if number <= spec.min_channels:
            continue
        if channel_is_occupied(channel):
            continue
        empty_since = empty_since_by_channel_id.get(channel.id)
        if empty_since is not None and now - empty_since >= spec.idle_seconds:
            delete_channel_ids.append(channel.id)

    return ReconcilePlan(delete_channel_ids=delete_channel_ids)


def refresh_empty_timers(
    channels: Iterable[ManagedVoiceChannel],
    empty_since_by_channel_id: dict[int, float],
    now: float,
    spec: ChannelPoolSpec,
) -> None:
    current_ids = set()

    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is None:
            continue
        current_ids.add(channel.id)

        if channel_is_occupied(channel):
            empty_since_by_channel_id.pop(channel.id, None)
        else:
            empty_since_by_channel_id.setdefault(channel.id, now)

    stale_ids = set(empty_since_by_channel_id) - current_ids
    for channel_id in stale_ids:
        empty_since_by_channel_id.pop(channel_id, None)


def base_channel[ChannelT: ManagedVoiceChannel](
    channels: Iterable[ChannelT], spec: ChannelPoolSpec
) -> ChannelT | None:
    managed: dict[int, ChannelT] = {}
    for channel in channels:
        number = parse_channel_number(channel.name, spec)
        if number is not None and number not in managed:
            managed[number] = channel

    return managed.get(1) or next(
        (channel for _, channel in sorted(managed.items())),
        None,
    )


def settings_fingerprint(channel: SyncableVoiceChannel) -> tuple[object, ...]:
    return tuple(getattr(channel, name) for name in SYNCED_ATTRIBUTES)


def channels_needing_sync[ChannelT: SyncableVoiceChannel](
    channels: Iterable[ChannelT], spec: ChannelPoolSpec
) -> list[ChannelT]:
    channel_list = list(channels)
    template = base_channel(channel_list, spec)
    if template is None:
        return []

    desired = settings_fingerprint(template)
    managed = canonical_managed_channels(channel_list, spec)
    return [
        channel
        for _, channel in sorted(managed.items())
        if channel.id != template.id and settings_fingerprint(channel) != desired
    ]


def pool_members[ChannelT: ManagedVoiceChannel](
    channels: Iterable[ChannelT], base: ChannelT, spec: ChannelPoolSpec
) -> list[ChannelT]:
    """The base channel and the extras named after it.

    Another channel that merely shares the base channel's name is not part of
    the pool: the base is known by ID, so the name is no longer ambiguous.
    """
    return [base] + [
        channel
        for channel in channels
        if channel.id != base.id and (parse_channel_number(channel.name, spec) or 0) > 1
    ]


def longest_name_too_long(spec: ChannelPoolSpec) -> bool:
    return len(spec.channel_name(spec.max_channels)) > MAX_CHANNEL_NAME_LENGTH


def pool_name_problems(specs: Sequence[ChannelPoolSpec]) -> list[str]:
    """Reasons a guild's pools, named after their base channels, cannot coexist."""
    problems: list[str] = []
    for spec in specs:
        if longest_name_too_long(spec):
            problems.append(
                f"{spec.channel_name(spec.max_channels)!r} would be longer than Discord's "
                f"{MAX_CHANNEL_NAME_LENGTH} character limit. Shorten the base channel's "
                "name or lower the maximum."
            )
    for index, spec in enumerate(specs):
        for other_index, other in enumerate(specs):
            if index == other_index:
                continue
            if spec.base_name == other.base_name:
                if index < other_index:
                    problems.append(
                        f"Two pools use base channels named {spec.base_name!r}. "
                        "Rename one of them in Discord."
                    )
            elif parse_channel_number(spec.base_name, other) is not None:
                problems.append(
                    f"{spec.base_name!r} looks like an extra channel of the "
                    f"{other.base_name!r} pool. Rename it in Discord or pick another channel."
                )
    return problems
