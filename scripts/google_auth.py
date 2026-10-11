"""Link Veda to your Google account (Gmail + Calendar), once. Same as `veda login`.

    python scripts/google_auth.py            # sign in; saves GOOGLE_REFRESH_TOKEN to .env
    python scripts/google_auth.py --check    # confirm the saved sign-in works and has every permission (no browser)
    python scripts/google_auth.py --manual   # headless machine (Raspberry Pi over SSH): open the address on any device and paste back
    python scripts/google_auth.py --browser  # force the local-browser flow
    python scripts/google_auth.py --port 8765  # fixed loopback port, if your OAuth client is a "Web application" type

Needs GOOGLE_API_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env (a Google Cloud OAuth client with the Gmail API and Google
Calendar API enabled). See src/controller/cli/google_login.py for how the two flows work.

Permissions asked for: read mail, create and send drafts, read and add calendar events. Veda never deletes mail or calendar events.

If your OAuth consent screen is still in "Testing", Google expires the refresh token after 7 days: set the publishing
status to "In production" (an unverified personal app is fine for your own account) to keep it.
The token is written to .env and never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from controller.cli import google_login  # noqa: E402
from core.env import load_env  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify the saved sign-in works")
    ap.add_argument("--manual", action="store_true", help="no browser here: open the address on another device, paste the result back")
    ap.add_argument("--browser", action="store_true", help="force the local-browser flow")
    ap.add_argument("--port", type=int, default=0, help="loopback port (default: any free port; 8765 with --manual)")
    args = ap.parse_args()
    load_env()
    if args.check:
        return google_login.run_check()
    return google_login.run_link(manual=True if args.manual else (False if args.browser else None), port=args.port)


if __name__ == "__main__":
    sys.exit(main())
