from __future__ import annotations
from app.logging_setup import configure_logging
configure_logging()

import argparse
import logging
import time
from imap_tools import MailBox, AND
from app.config import IMAP_HOST, IMAP_USER, IMAP_PASS, POLL_INTERVAL_SECONDS, OLLAMA_MODEL, DRY_RUN
from app.orchestrator import handle_email

logger = logging.getLogger("poller")


def run_test():
    logger.info("Running an offline test email through the pipeline (no mailbox needed)...")
    handle_email(
        sender_addr="customer@example.com",
        subject="Where is my order?",
        body="Hi, I ordered 9 days ago and it hasn't arrived. Order #4821.",
    )


def run_inbox_once():
    logger.debug("Connecting to IMAP host %s as %s", IMAP_HOST, IMAP_USER)
    with MailBox(IMAP_HOST).login(IMAP_USER, IMAP_PASS) as mailbox:
        messages = list(mailbox.fetch(AND(seen=False), limit=10))
        if not messages:
            logger.debug("No new unread messages this cycle.")
        for msg in messages:
            logger.info("New email -- from=%s subject=%r", msg.from_, msg.subject)
            handle_email(msg.from_, msg.subject, msg.text or msg.html)


def run_poll_loop():
    """Runs forever, checking the inbox every POLL_INTERVAL_SECONDS.
    This is the mode to run unattended -- see deploy/ for running it as a
    systemd service, or docker-compose.yml for the container version.
    """
    logger.info(
        "Poller starting. model=%s dry_run=%s poll_interval=%ss",
        OLLAMA_MODEL, DRY_RUN, POLL_INTERVAL_SECONDS,
    )
    while True:
        try:
            run_inbox_once()
        except Exception:
            # Never let one bad cycle kill the whole poller -- log the full
            # traceback and keep going.
            logger.exception("Error during this poll cycle -- will retry next interval.")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["test", "inbox", "poll"], default="test")
    args = parser.parse_args()

    if args.mode == "test":
        run_test()
    elif args.mode == "inbox":
        run_inbox_once()
    else:
        run_poll_loop()
