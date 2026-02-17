"""Entry point for ``python -m src.mcp_stdio``."""

import asyncio

from src.mcp_stdio.server import main

asyncio.run(main())
