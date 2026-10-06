"""The command line: login, run, once, status, install-startup, remove-startup."""

import argparse
import asyncio
import dataclasses
import sys

from watcher import startup
from watcher.amide_client import AmideAuthError, AmideClient, AmideUnavailable, make_http
from watcher.config import Config, ConfigError, load_config
from watcher.logs import get_logger
from watcher.queue import DiskQueue
from watcher.runner import Runner
from watcher.state import State


def format_status(config: Config, state: State, queue: DiskQueue, amide_state: str) -> str:
    """What the watcher knows, with no secret in it."""
    lines = [f"home: {config.home}", f"session: {'present' if config.session_path.exists() else 'missing'}", f"amide: {amide_state}",
             f"queue: {len(queue)} item{'' if len(queue) == 1 else 's'}, {queue.size_bytes()} bytes"]
    for chat_id, chat in sorted(state.chats.items()):
        lines.append(f"{chat_id}: last message {chat.last_id}, gone: {'yes' if chat.gone else 'no'}")
    return "\n".join(lines)


async def _amide_state(config: Config) -> str:
    async with make_http(config) as http:
        try:
            await AmideClient(http, config.amide_token).list_sources()
            return "token accepted"
        except AmideAuthError:
            return "token refused"
        except AmideUnavailable:
            return "not reachable"


async def _poll(config: Config, *, forever: bool) -> int:
    from watcher.telethon_client import TelethonClient
    log = get_logger(config.log_path)
    tg = TelethonClient(config)
    while True:                                            # at sign-in the network may not be up yet
        try:
            await tg.connect()
            break
        except Exception as exc:
            log.error("Could not reach Telegram (%s)", type(exc).__name__)
            if not forever:
                return 1
            await asyncio.sleep(30)
    try:
        if not await tg.is_authorized():
            log.error("Not signed in to Telegram. Run: python -m watcher login")
            return 2
        async with make_http(config) as http:
            runner = Runner(tg, AmideClient(http, config.amide_token), State(config.state_path), DiskQueue(config.queue_dir), config, log=log)
            while True:
                try:
                    await runner.poll_once()
                except Exception as exc:                   # never print message text or secrets: only the error's class
                    log.error("Poll failed (%s)", type(exc).__name__)
                if not forever:
                    return 0
                await asyncio.sleep(config.poll_seconds)
    finally:
        await tg.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="watcher", description="Hands price lists from Telegram groups to Amide.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("login", "run", "status", "install-startup", "remove-startup"):
        sub.add_parser(name)
    once = sub.add_parser("once")
    once.add_argument("--days", type=int, default=None)
    args = parser.parse_args(argv)

    if args.command == "install-startup":
        return startup.install()
    if args.command == "remove-startup":
        return startup.remove()
    try:
        config = load_config()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command == "status":
        print(format_status(config, State(config.state_path), DiskQueue(config.queue_dir), asyncio.run(_amide_state(config))))
        return 0
    if args.command == "login":
        from watcher.telethon_client import TelethonClient

        async def login():
            tg = TelethonClient(config)
            await tg.connect()
            try:
                await tg.login_interactive()
            finally:
                await tg.close()
        asyncio.run(login())
        print(f"Signed in. The session is saved in {config.home}.")
        return 0
    if args.command == "once":
        if args.days is not None:
            config = dataclasses.replace(config, backfill_days=max(1, min(args.days, 60)))
        return asyncio.run(_poll(config, forever=False))
    try:
        return asyncio.run(_poll(config, forever=True))
    except KeyboardInterrupt:
        return 0
