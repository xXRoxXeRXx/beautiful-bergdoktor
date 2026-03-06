# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install Flask (only external dependency)
RUN pip install --no-cache-dir flask

# Copy all application files
COPY checker.py .
COPY database.py .
COPY notifyDoctolibDoctorsAppointment.py .
COPY entrypoint.py .
COPY web/ ./web/

# Create data directory
RUN mkdir -p /data

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app /data

USER app

# Single entrypoint for both bot loop and web interface
CMD ["python", "entrypoint.py"]