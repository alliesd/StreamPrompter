"""Entry point used when building the standalone executable."""

import sys

from stream_prompter.app import main

if __name__ == "__main__":
    sys.exit(main())
