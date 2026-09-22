"""Run Amide locally: `python -m app` (add --reload while developing).

Host and port come from AMIDE_HOST / AMIDE_PORT.
"""

import os
import sys

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("AMIDE_HOST", "127.0.0.1"),
        port=int(os.environ.get("AMIDE_PORT", "1707")),
        reload="--reload" in sys.argv[1:],
    )


if __name__ == "__main__":
    main()
