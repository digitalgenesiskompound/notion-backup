# Use a lightweight Python image
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Install tzdata for timezone handling
RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install dependencies
COPY requirements-notion.txt .
RUN pip install --no-cache-dir -r requirements-notion.txt

# Copy the script and other necessary files
COPY notion_export.py .
COPY .env .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the timezone based on the .env file
ARG TIMEZONE
ENV TZ=${TIMEZONE}
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone

# Define the entrypoint
ENTRYPOINT ["python", "notion_export.py"]

# Default command arguments
CMD []
