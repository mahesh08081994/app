# Pins the exact Python version -- this is what permanently fixes the
# "str | None needs Python 3.10+" class of bug we hit on the host's
# Python 3.9. Same image is used for both the poller and the review app;
# docker-compose.yml picks which command each container runs.
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (separate layer -- only rebuilds this step
# when requirements.txt actually changes, not on every code edit)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now the application code
COPY app/ ./app/
COPY main.py review_app.py build_index.py ./

# Overridden per-service in docker-compose.yml -- this is just a sane default
CMD ["python", "main.py", "--mode", "poll"]
