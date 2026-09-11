import os
from pathlib import Path
import httpx
from dotenv import load_dotenv, find_dotenv
from flask import Flask, request, jsonify, send_from_directory

# Load environment variables from .env
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path) if env_path.exists() else load_dotenv(find_dotenv())

# Define frontend directory absolute path
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "Frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "Weather.html")

@app.route("/data-collection/weather")
def get_weather():
    city = request.args.get("city")
    lat = request.args.get("lat")
    lon = request.args.get("lon")

    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        return jsonify({"detail": "API key not configured"}), 500

    params = {"appid": api_key, "units": "metric"}
    if city:
        params["q"] = city
    elif lat and lon:
        params["lat"] = lat
        params["lon"] = lon
    else:
        return jsonify({"detail": "City or lat/lon parameters are required"}), 400

    try:
        response = httpx.get("https://api.openweathermap.org/data/2.5/weather", params=params)
        if response.status_code != 200:
            return jsonify(response.json()), response.status_code
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"detail": str(e)}), 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
