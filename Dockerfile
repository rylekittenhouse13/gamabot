# Dockerfile

# Use a slim Python image for a smaller final image size.
# Bookworm is the stable Debian 12 release.
FROM python:3.11-slim-bookworm AS base

# Set environment variables for non-interactive install and pyppeteer.
# This improves compatibility and logging within Docker.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV DOCKER_ENV=true

# Set working directory inside the container.
WORKDIR /app

# Install system dependencies required by pyppeteer/chromium.
# This is crucial for the meta-ai-api library to function headless.
RUN apt-get update && \
	apt-get install -y --no-install-recommends \
	chromium \
	libnss3 \
	# Clean up apt cache to reduce final image size.
	&& apt-get clean && \
	rm -rf /var/lib/apt/lists/*

# Install Python dependencies.
# Copy requirements first to leverage Docker's layer caching for faster rebuilds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container.
COPY . .

# Command to run the application when the container starts.
CMD ["python", "bot.py"]