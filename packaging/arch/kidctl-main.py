# Einstieg für /usr/bin/kidctl (als Skriptdatei, nicht per „python -c“: die Programm-Whitelist
# sperrt Inline-Code für Kinder; Skripte unter /usr/lib/kidctl sind immer erlaubt).
import sys

from kidctl_agent.cli import main

sys.exit(main())
