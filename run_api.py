"""Run the FastAPI dashboard. Usage: python run_api.py"""
import os
from pathlib import Path

# Load .env first so MONGODB_URI is available before any SSL/DB code runs.
_load_env = Path(__file__).resolve().parent / ".env"
if _load_env.exists():
    from dotenv import load_dotenv
    load_dotenv(_load_env)

# MongoDB Atlas + Python (especially on Windows) can raise TLSV1_ALERT_INTERNAL_ERROR
# unless the default SSL context uses a proper CA bundle. Set these before any import
# that touches SSL (e.g. pymongo).
_uri = os.environ.get("MONGODB_URI", "")
if "mongodb+srv://" in _uri:
    try:
        import certifi
        _ca = certifi.where()
        os.environ["SSL_CERT_FILE"] = _ca
        os.environ["REQUESTS_CA_BUNDLE"] = _ca
    except ImportError:
        pass

import uvicorn
from api.main import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
