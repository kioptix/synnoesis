"""Zero-install doorway (A) -- run straight from the clone.

    python synnoesis.py send --to bob "hello"
    python synnoesis.py read --agent-id bob --since 2h

No install, no PYTHONPATH -- works exactly like ``python comms/send.py`` today.
This is a thin shim; all routing lives in ``synnoesis/cli.py::main``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synnoesis.cli import main

sys.exit(main())
