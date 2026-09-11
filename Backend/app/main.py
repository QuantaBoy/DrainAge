import sys
from pathlib import Path

# Ensure the root 'Backend' directory is in sys.path for absolute package imports
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.routes import weather

# Load environment configuration
env_file = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_file)

# Initialize FastAPI application
app = FastAPI(
    title="Weather Data Collection API",
    description="Backend API service for collecting and serving real-time weather metrics.",
    version="1.0.0",
)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(weather.router)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


