FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libopus0 \
    libopus-dev \
    ffmpeg \
    libsndfile1 \
    curl \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-descarga el modelo faster-whisper al construir la imagen
ARG WHISPER_MODEL=small
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"

COPY . .

EXPOSE 16000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "16000", "--log-level", "info"]
