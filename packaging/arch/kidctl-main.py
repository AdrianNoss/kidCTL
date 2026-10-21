# Einstieg für /usr/bin/kidctl (als Skriptdatei, nicht per „python -c“: die Programm-Whitelist
# sperrt Inline-Code für Kinder; Skripte unter /usr/lib/kidctl sind immer erlaubt).
import os
import sys

# Mitgebrachte Bibliotheken für diese Python-Version (nur im .deb für Ubuntu 24.04 & Co., siehe vendor.txt)
_vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "py%d.%d" % sys.version_info[:2])
if os.path.isdir(_vendor):
    sys.path.insert(0, _vendor)

from kidctl_agent.cli import main

sys.exit(main())
