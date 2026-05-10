FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p state logs

# Declare /app/state as a volume mount point.
# On Railway: create a Volume in the dashboard and mount it at /app/state.
VOLUME ["/app/state"]

CMD ["python", "main.py"]
