# Einstieg für /usr/bin/kidctl-tray (Skriptdatei – siehe kidctl-main.py).
import os
import sys

# Mitgebrachte Bibliotheken für diese Python-Version (nur im .deb für Ubuntu 24.04 & Co., siehe vendor.txt)
_vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "py%d.%d" % sys.version_info[:2])
if os.path.isdir(_vendor):
    sys.path.insert(0, _vendor)

from kidctl_tray.app import main

sys.exit(main())
