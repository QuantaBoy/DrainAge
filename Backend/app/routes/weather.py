import os
from typing import Any, Dict
import httpx
from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter(prefix="/data-collection", tags=["Weather"])

OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


@router.get("/weather", response_model=Dict[str, Any], summary="Fetch Weather Data")
async def get_weather(city: str = Query(..., description="Name of the city to query weather for")) -> Dict[str, Any]:
    """
    Fetches real-time weather metrics for a specified city from the OpenWeatherMap API.
    """
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OpenWeather API key is not configured on the server."
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                OPENWEATHER_URL,
                params={"q": city, "appid": api_key, "units": "metric"}
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Failed to communicate with OpenWeather service: {exc}"
            )

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json()
        )

    return response.json()

