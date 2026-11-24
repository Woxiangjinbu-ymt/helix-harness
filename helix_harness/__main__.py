"""Launch the local Helix Harness API."""

import os

import uvicorn


def main():
    uvicorn.run(
        "helix_harness.api:app",
        host="127.0.0.1",
        port=int(os.getenv("RSI_PORT", "8765")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
