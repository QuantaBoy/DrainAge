# The flood engine: the FastAPI app with the city model built into the image.
# Built and run by Hugging Face Spaces (see DEPLOY.md); any Docker host works the same.

FROM python:3.12-slim

# Spaces run the container as user 1000; building as that user keeps the model cache
# writable at run time.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

# Build the drain network, the terrain, the coupled model and the street graph now,
# so the running server loads them from disk in seconds instead of building them
# for minutes on every start.
RUN python -c "from app.services import drain_network, forecast, routing, street_index; \
drain_network.load_drains(); street_index.load(); forecast.warm(); routing.warm(forecast.surface_model())"

EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
