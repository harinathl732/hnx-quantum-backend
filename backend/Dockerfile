# Use official light-weight Python image
FROM python:3.10-slim

# Set workspace environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory inside container
WORKDIR /code

# Install system utilities (like gcc for native packages, PostgreSQL clients, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependencies
COPY requirements.txt /code/

# Install dependencies
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy source code files
COPY . /code/

# Expose port
EXPOSE 8000
