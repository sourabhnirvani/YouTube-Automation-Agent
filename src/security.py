# FILE: src/security.py
# Automated security filter: Redacts sensitive API keys and tokens from stdout, stderr, and logging.

import sys
import re
import logging

class RedactingStream:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        self.patterns = [
            re.compile(r'nvapi-[a-zA-Z0-9_\-]+'),
            re.compile(r'[0-9]{8,10}:[a-zA-Z0-9_\-]{35}'),
            re.compile(r'AQ\.[a-zA-Z0-9_\-]+'),
            re.compile(r'7ChK[a-zA-Z0-9_\-]+')
        ]

    @property
    def encoding(self):
        return getattr(self.original_stream, 'encoding', 'utf-8')

    def write(self, data):
        if not data:
            return
        if not isinstance(data, str):
            try:
                self.original_stream.write(data)
            except Exception:
                pass
            return
        
        redacted = data
        for pattern in self.patterns:
            redacted = pattern.sub('[REDACTED_API_KEY]', redacted)
        self.original_stream.write(redacted)

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.original_stream, name)


class RedactingFormatter(logging.Formatter):
    def __init__(self, orig_formatter=None):
        super().__init__()
        self.orig_formatter = orig_formatter or logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        self.patterns = [
            re.compile(r'nvapi-[a-zA-Z0-9_\-]+'),
            re.compile(r'[0-9]{8,10}:[a-zA-Z0-9_\-]{35}'),
            re.compile(r'AQ\.[a-zA-Z0-9_\-]+'),
            re.compile(r'7ChK[a-zA-Z0-9_\-]+')
        ]

    def format(self, record):
        formatted = self.orig_formatter.format(record)
        for pattern in self.patterns:
            formatted = pattern.sub('[REDACTED_API_KEY]', formatted)
        return formatted


def init_security():
    """Initializes stream and log redirection to prevent API key leaks."""
    # Only initialize once
    if isinstance(sys.stdout, RedactingStream):
        return

    sys.stdout = RedactingStream(sys.stdout)
    sys.stderr = RedactingStream(sys.stderr)

    # Wrap existing handler formatters
    for handler in logging.root.handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.formatter = RedactingFormatter(handler.formatter)

    # Wrap all future handlers setFormatter method
    orig_set_formatter = logging.Handler.setFormatter
    def new_set_formatter(self, formatter):
        if formatter and not isinstance(formatter, RedactingFormatter):
            formatter = RedactingFormatter(formatter)
        orig_set_formatter(self, formatter)
    logging.Handler.setFormatter = new_set_formatter
