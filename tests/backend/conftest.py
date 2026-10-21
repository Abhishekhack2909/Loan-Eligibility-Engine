from __future__ import annotations

import sys
from pathlib import Path


# The application is intentionally kept as ``backend/`` at the workspace
# root, while the backend project metadata lives inside that directory.
# Make test imports deterministic from either workspace or backend cwd.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
