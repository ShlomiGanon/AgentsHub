import os
import sys
import time
import subprocess
import logging
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stack_runner")

def main():
    load_dotenv('.env')
    profile_name = "profiles.unified_test"
    python_exe = sys.executable

    logger.info("Starting AgentsHub Stack for %s...", profile_name)

    # profiles.unified_test no longer seeds its mock/demo data (and
    # provisions the bot-service identity) as an import side effect — this
    # is the one explicit call site for that, run once from this parent
    # process before either the API or bot subprocess starts, so both
    # inherit an already-seeded deployment instead of racing to seed it
    # themselves. See profiles.unified_test.ensure_seed_data.
    from profiles import unified_test
    unified_test.ensure_seed_data()

    # Clean stale locks
    lock_file = Path("data/unified_test/unified_history.db.bot.lock")
    if lock_file.exists():
        try:
            lock_file.unlink()
            logger.info("Removed stale lock file: %s", lock_file)
        except Exception as e:
            logger.warning("Could not remove lock file: %s", e)

    env = os.environ.copy()

    # Open log files
    api_stdout = open("api-teamstatus-final.stdout.log", "a", encoding="utf-8")
    api_stderr = open("api-teamstatus-final.stderr.log", "a", encoding="utf-8")
    bot_stdout = open("bot-teamstatus-final.stdout.log", "a", encoding="utf-8")
    bot_stderr = open("bot-teamstatus-final.stderr.log", "a", encoding="utf-8")

    api_proc = subprocess.Popen(
        [python_exe, "-m", "api.app", profile_name],
        stdout=api_stdout,
        stderr=api_stderr,
        env=env,
    )
    logger.info("API started (PID: %d)", api_proc.pid)

    # Wait for API to initialize
    time.sleep(5)

    bot_proc = subprocess.Popen(
        [python_exe, "-m", "bot.app", profile_name],
        stdout=bot_stdout,
        stderr=bot_stderr,
        env=env,
    )
    logger.info("Bot started (PID: %d)", bot_proc.pid)

    try:
        while True:
            time.sleep(2)
            api_poll = api_proc.poll()
            bot_poll = bot_proc.poll()
            if api_poll is not None:
                logger.error("API process exited unexpectedly with code %d", api_poll)
                break
            if bot_poll is not None:
                logger.error("Bot process exited unexpectedly with code %d", bot_poll)
                break
    except KeyboardInterrupt:
        logger.info("Stopping stack...")
    finally:
        if api_proc.poll() is None:
            api_proc.terminate()
        if bot_proc.poll() is None:
            bot_proc.terminate()
        api_stdout.close()
        api_stderr.close()
        bot_stdout.close()
        bot_stderr.close()

if __name__ == "__main__":
    main()
