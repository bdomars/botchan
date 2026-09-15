from botchan.client import BotChan
from botchan.database_config import PostgresConfigSource
from botchan.settings import load_bot_config


def main() -> None:
    bot_config = load_bot_config()
    print(f"Git revision: {bot_config.git_rev}", flush=True)
    bot = BotChan(PostgresConfigSource(bot_config.database_url))
    bot.run(bot_config.token, log_level=bot_config.log_level, root_logger=True)
