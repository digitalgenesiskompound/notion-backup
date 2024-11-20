# Use a lightweight Python image
FROM python:3.9-alpine

# Set the working directory
WORKDIR /app

# Install dependencies, including tzdata for time zone data
RUN apk add --no-cache gcc musl-dev libffi-dev tzdata

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script and other necessary files
COPY notion_export.py .
COPY .env .

# Add a non-root user
RUN addgroup -S appgroup && adduser -S notionbackup -G notionbackup

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Set the timezone based on the .env file
ARG TIMEZONE
ENV TZ=${TIMEZONE}
RUN ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime && echo ${TZ} > /etc/timezone

# Use non-root user
USER notionbackup

# Run the script
CMD ["python", "notion_export.py"]
