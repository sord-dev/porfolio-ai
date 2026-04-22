FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY app/requirements.txt .

# Install system dependencies for health check
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ .

# Expose port
EXPOSE 8765

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash portfolio && \
    chown -R portfolio:portfolio /app
USER portfolio

# Command to run the application
CMD ["python", "main.py"]