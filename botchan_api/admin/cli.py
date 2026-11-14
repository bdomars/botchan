"""Grant, revoke and list BotChan operators.

    python -m botchan_api.admin.cli grant <discord_user_id> [--note TEXT]
    python -m botchan_api.admin.cli revoke <discord_user_id>
    python -m botchan_api.admin.cli list

Only needs DATABASE_URL. Revoking also ends the operator's admin sessions.
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from botchan_api.common import now
from botchan_api.guilds import valid_snowflake
from botchan_api.models import AdminSession, Operator


async def grant(session_factory: async_sessionmaker, user_id: str, note: str | None) -> bool:
    """Add an operator. Returns False if they already were one (the note is updated)."""
    async with session_factory() as db:
        existing = await db.get(Operator, user_id)
        if existing is not None:
            if note is not None:
                existing.note = note
                await db.commit()
            return False
        db.add(Operator(discord_user_id=user_id, note=note, created_at=now()))
        await db.commit()
        return True


async def revoke(session_factory: async_sessionmaker, user_id: str) -> bool:
    """Remove an operator and their admin sessions. Returns False if they were not one."""
    async with session_factory() as db:
        await db.execute(delete(AdminSession).where(AdminSession.discord_user_id == user_id))
        result = await db.execute(delete(Operator).where(Operator.discord_user_id == user_id))
        await db.commit()
        return bool(getattr(result, "rowcount", 0))


async def list_operators(session_factory: async_sessionmaker) -> list[Operator]:
    async with session_factory() as db:
        return list((await db.scalars(select(Operator).order_by(Operator.created_at))).all())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m botchan_api.admin.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    grant_parser = commands.add_parser("grant", help="let a Discord user into the admin app")
    grant_parser.add_argument("user_id")
    grant_parser.add_argument("--note", help="who this is, for the operator list")
    revoke_parser = commands.add_parser("revoke", help="remove an operator")
    revoke_parser.add_argument("user_id")
    commands.add_parser("list", help="list operators")
    args = parser.parse_args(argv)
    if args.command in {"grant", "revoke"} and not valid_snowflake(args.user_id):
        parser.error(f"{args.user_id!r} is not a Discord user ID")
    return args


async def run(args: argparse.Namespace, database_url: str) -> int:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if args.command == "grant":
            added = await grant(session_factory, args.user_id, args.note)
            print(f"{args.user_id} {'is now' if added else 'was already'} an operator")
        elif args.command == "revoke":
            removed = await revoke(session_factory, args.user_id)
            print(f"{args.user_id} {'is no longer' if removed else 'was not'} an operator")
        else:
            for operator in await list_operators(session_factory):
                note = f"  {operator.note}" if operator.note else ""
                print(f"{operator.discord_user_id}  {operator.created_at:%Y-%m-%d}{note}")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    return asyncio.run(run(args, database_url))


if __name__ == "__main__":
    sys.exit(main())
