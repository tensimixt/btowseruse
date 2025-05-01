# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies needed for playwright (though browser is remote)
# Might not be strictly necessary if only connecting remotely, but good practice
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     # Add any system deps if needed
#     && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies from requirements.txt
# Using the virtual environment is not standard practice inside Docker, install directly
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY ./src ./src
COPY ./venv ./venv # Copy venv just in case, though dependencies are installed globally

# Create recordings directory and set permissions if needed
RUN mkdir /app/recordings && chown -R nobody:nogroup /app/recordings
ENV RECORDINGS_DIR=/app/recordings

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Define environment variables (can be overridden at runtime)
# These point to the browser service within the Docker network
ENV BROWSER_CDP_ENDPOINT="ws://browser:9222"
ENV VNC_HOST="browser"
ENV VNC_PORT="5901"

# Run main.py when the container launches
# Use the non-debug mode for testing deployment scenario
CMD ["python", "src/main.py"]

