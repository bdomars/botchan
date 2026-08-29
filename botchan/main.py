from botchan.client import BotChan
from botchan.config import load_runtime_config
from botchan.settings import load_bot_config


def main() -> None:
    bot_config = load_bot_config()
    runtime_config = load_runtime_config()
    print(f"Git revision: {bot_config.git_rev}", flush=True)
    bot = BotChan(runtime_config)
    bot.run(bot_config.token, log_level=bot_config.log_level, root_logger=True)
