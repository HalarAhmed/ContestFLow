"""Run the scheduled jobs: contest monitor, practice sync, post-contest analysis."""
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import settings
from utils.logging import setup_logging, get_logger
from jobs.contest_monitor import run_contest_monitor
from jobs.practice_sync import run_practice_sync
from jobs.post_contest import run_post_contest_analysis

setup_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)


def job_contest_monitor():
    try:
        run_contest_monitor("default")
    except Exception as e:
        logger.exception("Contest monitor job failed: %s", e)


def job_practice_sync():
    try:
        run_practice_sync("default")
    except Exception as e:
        logger.exception("Practice sync job failed: %s", e)


def job_post_contest():
    try:
        run_post_contest_analysis("default")
    except Exception as e:
        logger.exception("Post-contest job failed: %s", e)


def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(job_contest_monitor, IntervalTrigger(minutes=30), id="contest_monitor")
    scheduler.add_job(job_practice_sync, IntervalTrigger(hours=6), id="practice_sync")
    scheduler.add_job(job_post_contest, IntervalTrigger(hours=1), id="post_contest")
    logger.info("Scheduler started: contest monitor every 30 min, practice sync every 6 h, post-contest every 1 h")
    scheduler.start()


if __name__ == "__main__":
    main()
