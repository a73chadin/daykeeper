"""Entry point: python main.py [path_to_vault_file]."""

import sys

from daykeeper.boot import apply_theme

apply_theme(sys.argv[1:])  # before the window's modules: they take colours when imported

from daykeeper.app import main  # noqa: E402

if __name__ == "__main__":
    main()
