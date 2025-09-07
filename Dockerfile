FROM python:3.10-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and binaries
COPY bin/ ./bin/
COPY . .

ENTRYPOINT ["python", "main.py"]
