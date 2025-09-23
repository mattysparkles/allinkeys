import os
from .app import app

def main():
    import uvicorn
    host = os.getenv("TELEMETRY_SERVICE_HOST", "0.0.0.0")
    try:
        port = int(os.getenv("TELEMETRY_SERVICE_PORT", "8000"))
    except Exception:
        port = 8000
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
