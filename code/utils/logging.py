"""
Thread-local logging module that provides the same interface as the standard logging module.

Usage:
Replace `import logging` with `from utils import logging` in your files.
All logging.info(), logging.warning(), logging.error() calls will work exactly the same.
"""

import threading
import logging as _logging
import os
from pathlib import Path
from typing import Optional

# Thread/Process-local storage for the current analysis logger
_local = threading.local()

def _get_current_logger():
    """Get the current thread-local logger, fallback to standard logging if none set."""
    return getattr(_local, 'logger', _logging.getLogger())

# Public interface - matches standard logging module
def info(msg:object, *args: object, **kwargs: object) -> None:
    """Log a message with severity 'INFO'."""
    _ = _get_current_logger().info(msg, *args, **kwargs)

def warning(msg:object, *args: object, **kwargs: object) -> None:
    """Log a message with severity 'WARNING'."""
    _ = _get_current_logger().warning(msg, *args, **kwargs)

def error(msg:object, *args: object, **kwargs: object) -> None:
    """Log a message with severity 'ERROR'."""
    _ = _get_current_logger().error(msg, *args, **kwargs)

# Additional common logging levels if needed
def debug(msg:object, *args: object, **kwargs: object) -> None:
    """Log a message with severity 'DEBUG'."""
    _ = _get_current_logger().debug(msg, *args, **kwargs)

def critical(msg:object, *args: object, **kwargs: object) -> None:
    """Log a message with severity 'CRITICAL'."""
    _ = _get_current_logger().critical(msg, *args, **kwargs)

# Setup and teardown functions
def setup_logger_for_repository(log_path: Path, repo_name: str) -> _logging.Logger:
    """Set up a thread-local logger for the current repository analysis."""
    # Create a unique logger name
    logger_name = f"repo_analysis_{repo_name}_{os.getpid()}_{threading.get_ident()}"
    logger = _logging.getLogger(logger_name)

    # Clear any existing handlers
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

    # Create and configure the file handler
    file_handler = _logging.FileHandler(log_path, mode='w', encoding='utf-8')
    file_handler.setLevel(_logging.INFO)

    # Create formatter
    formatter = _logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(file_handler)
    logger.setLevel(_logging.INFO)
    logger.propagate = False

    # Store in thread-local storage
    _local.logger = logger
    _local.logger_name = logger_name

    return logger

def cleanup_repository_logger():
    """Clean up the current thread-local logger."""
    if hasattr(_local, 'logger'):
        logger = _local.logger
        logger_name = _local.logger_name

        # Clean up handlers
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

        # Remove from logging registry
        if logger_name in _logging.Logger.manager.loggerDict:
            del _logging.Logger.manager.loggerDict[logger_name]

        # Clear thread-local storage
        delattr(_local, 'logger')
        delattr(_local, 'logger_name')

def get_current_logger() -> Optional[_logging.Logger]:
    """Get the current thread-local logger if one is set."""
    return getattr(_local, 'logger', None)

def has_repository_logger() -> bool:
    """Check if a repository logger is currently set up."""
    return hasattr(_local, 'logger')

# Export standard logging constants and classes if needed
DEBUG = _logging.DEBUG
INFO = _logging.INFO
WARNING = _logging.WARNING
ERROR = _logging.ERROR
CRITICAL = _logging.CRITICAL

# For compatibility, expose some standard logging functions
getLogger = _logging.getLogger
basicConfig = _logging.basicConfig
shutdown = _logging.shutdown

# Context manager for easy setup/cleanup
class RepositoryLoggerContext:
    """Context manager for repository logging setup."""

    def __init__(self, log_path: Path, repo_name: str):
        self.log_path = log_path
        self.repo_name = repo_name
        self.logger = None

    def __enter__(self):
        self.logger = setup_logger_for_repository(self.log_path, self.repo_name)
        return self.logger

    def __exit__(self, exc_type, exc_val, exc_tb):
        cleanup_repository_logger()

# Convenience function to create context manager
def repository_logger(log_path: Path, repo_name: str):
    """Create a context manager for repository logging."""
    return RepositoryLoggerContext(log_path, repo_name)
