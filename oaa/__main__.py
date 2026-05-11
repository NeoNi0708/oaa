"""OAA CLI entry point (for development mode)"""
import sys

from .cli import cli

if __name__ == "__main__":
    sys.exit(cli())
