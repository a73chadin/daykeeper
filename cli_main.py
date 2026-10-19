"""Entry point of the console helper exe (daykeeper-cli.exe); see daykeeper/cli.py."""

import sys

from daykeeper.cli import main

if __name__ == "__main__":
    sys.exit(main())
