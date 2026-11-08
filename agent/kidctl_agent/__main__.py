import sys

# Windows-Dienst im abgesicherten Modus: vor dem Import von cli (asyncio ist dort ohne Netzwerk-Stack nicht ladbar)
if sys.platform == "win32" and sys.argv[1:] == ["service"]:
    from .win import safemode

    if safemode.safe_boot():
        sys.exit(safemode.run())

from .cli import main  # noqa: E402

sys.exit(main())
