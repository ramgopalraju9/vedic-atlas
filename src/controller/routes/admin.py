
"""Admin routes — shutdown the server from the UI.

Donor: veda/routes/admin.py, copied verbatim.
"""

import os
import signal
import threading

from fastapi import APIRouter

from core.logging_config import logger
