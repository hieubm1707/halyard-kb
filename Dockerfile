FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/tmp/articles

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY kbsync ./kbsync

RUN useradd --create-home app
USER app

# Secrets are passed at run time (docker run -e API_KEY=...), never baked into the image.
CMD ["python", "main.py"]
