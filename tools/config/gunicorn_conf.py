"""
Gunicorn configuration file with memory optimization hooks.
"""

import gc
import logging
import os

# Configure logger
logger = logging.getLogger(__name__)

# Basic Gunicorn settings (can be overridden by environment variables)
bind = os.getenv("BIND", "0.0.0.0:1234")
# Reduced from 4 to 2 workers to prevent OOM kills (each worker can use 2-5GB)
workers = int(os.getenv("WORKERS", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.getenv("TIMEOUT", "600"))

# Memory optimization settings
max_requests = int(
    os.getenv("MAX_REQUESTS", "1000"),
)  # Recycle workers after N requests
max_requests_jitter = int(
    os.getenv("MAX_REQUESTS_JITTER", "100"),
)  # Add randomness to prevent all workers recycling at once

# Worker memory limit (restart worker if exceeds this)
worker_tmp_dir = "/dev/shm"  # Use shared memory for better performance  # noqa: S108

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"  # Log to stderr
loglevel = os.getenv("LOG_LEVEL", "info")


def post_fork(server, worker):
    """
    Called just after a worker has been forked.
    """
    server.log.info("Worker spawned (pid: %s)", worker.pid)


def pre_fork(server, worker):
    """
    Called just before a worker is forked.
    """


def worker_exit(server, worker):
    """
    Called just after a worker has been exited, in the master process.
    This is where we can clean up resources.
    """
    server.log.info("Worker exiting (pid: %s)", worker.pid)


def on_exit(server):
    """
    Called just before the master process exits.
    """
    server.log.info("Shutting down: cleaning up resources")


def worker_int(worker):
    """
    Called when a worker receives the SIGINT or SIGQUIT signal.
    """
    worker.log.info("Worker received INT/QUIT signal (pid: %s)", worker.pid)


def worker_abort(worker):
    """
    Called when a worker is aborted (SIGABRT).
    This can happen when the worker runs out of memory or has other critical errors.
    """
    worker.log.error(
        "Worker aborted (pid: %s) - likely OOM or critical error",
        worker.pid,
    )
    # Force garbage collection
    gc.collect()
