"""Start a loopback-only local game server."""

import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "werewolf.server:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", "8765")),
        access_log=False,
    )
