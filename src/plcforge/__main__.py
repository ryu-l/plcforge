"""Allow ``python -m plcforge`` alongside the installed ``plcforge`` script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
