import os

import uvicorn
from main import app


if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("CHECKWORD_SERVER_PORT", "8765"))
    host = os.environ.get("CHECKWORD_SERVER_HOST", "0.0.0.0")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning",
    )
