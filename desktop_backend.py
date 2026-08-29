"""Local-only FastAPI launcher used by the frozen desktop backend."""

from __future__ import annotations

import logging
import os

import uvicorn

from api.app import app


def _api_port() -> int:
    raw_value = os.getenv("COC_API_PORT", "8000")
    try:
        port = int(raw_value)
    except ValueError as error:
        raise SystemExit(f"COC_API_PORT must be an integer, received: {raw_value!r}") from error
    if not 1 <= port <= 65535:
        raise SystemExit(f"COC_API_PORT must be between 1 and 65535, received: {port}")
    return port


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    port = _api_port()
    logging.info("Starting bundled local API at http://127.0.0.1:%s", port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
