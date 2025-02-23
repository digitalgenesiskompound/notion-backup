## I have had to stop working on this due to life. It will not receieve any updates for the forseeable future. I will be taking down this repo by summer 2025 if I can't get back to working on it.

# Notion Backup Tool

I do this as a hobby from the information that I have learned in school. If you would like, you can buy me a [**coffee**](https://www.paypal.com/donate/?hosted_button_id=KNKHSKAL4YWVJ) 🍵.

### Features

- Export pages, databases, images, files and pdf's from your Notion workspace.
- Databases are stored as **.csv** files and normal text pages are stored as **Markdown / .md** files. Images, files and pdf's are stored in their original formats.
- Store backups locally, on Backblaze B2 cloud storage, or both.
- Schedule backups at intervals you define (Hourly, Daily, Weekly, Monthly).
- Customize the time of day backups run.
- Dockerized for easy deployment on any system that supports Docker.

### Prerequisites

- **Notion API Token**: You'll need an integration token from Notion to access your workspace.
- **Backblaze B2 Account** (optional): If you wish to upload backups to Backblaze B2.
- **Docker and Docker Compose**: For running the application in a containerized environment.

# Installation

### 1. Setup Your Environment

- Install [Docker](https://docs.docker.com/engine/install/) then:

```bash
sudo apt update && sudo apt upgrade
sudo apt install git
```

### 2. Clone the Repository

```bash
sudo git clone https://github.com/digitalgenesiskompound/notion-backup.git
cd notion-backup
```

## Configuration

### 1. Obtain a Notion API Token

- Go to Notion Integrations.
- Click on **"New integration"**.
- Enter a name for your integration and select the workspace you want to access.
- Copy the **Internal Integration Token** and save it for later.

### 2. (Optional) Set Up Backblaze B2 Credentials

If you wish to upload backups to Backblaze B2:

- Log in to your Backblaze B2 account.
- Navigate to **App Keys** under the **Buckets** section.
- Click on **"Add a New Application Key"**.
- Set the **Name** and **Capabilities** (ensure it has permissions for the actions you need).
- Copy the **KeyID** and **Application Key**.
- Note your **Bucket Name** and **Bucket's Endpoint URL**.

### 3. Configure the `.env` File

- Rename the `.env.example` file to `.env`:

```bash
mv .env.example .env
```

- Open the `.env` file in a text editor and configure the variables for your setup.

Using Linux's Nano:
```bash
sudo nano .env
```

# Usage

### Run with Docker Compose

### 1. Build and Start the Container

```bash
docker compose up -d
```

### 2. View Logs

```bash
docker compose logs -f
```

This will follow the logs of the container, CTRL + C to stop following logs.

### 3. Success

- If you correctly configured the environment variables than you should be seeing your backups from Notion as per scheduled request.

## Scheduling Backups

### Run a Manual Backup Immediatly

```bash
docker compose run --rm notion-backup --run-now
```

The backup schedule is controlled by the `BACKUP_INTERVAL` and `BACKUP_TIME` variables in the `.env` file.

- **BACKUP_INTERVAL**:
    - `Hourly`: Runs every hour at the minute specified in `BACKUP_TIME`.
    - `Daily`: Runs every day at the time specified in `BACKUP_TIME`.
    - `Weekly`: Runs every week on Monday at the time specified in `BACKUP_TIME`.
    - `Monthly`: Runs every 28 days at the time specified in `BACKUP_TIME`.
- **BACKUP_TIME**:
    - Specify the time in 24-hour format `HH:MM`, e.g., `23:30` for 11:30 PM.

### Example Configurations

- **Hourly at 15 minutes past the hour**:
    
    ```
    ACKUP_INTERVAL=Hourly
    BACKUP_TIME=00:15
    ```
    
- **Daily at 2:00 AM**:
    
    ```
    BACKUP_INTERVAL=Daily
    BACKUP_TIME=02:00
    ```
    
- **Weekly on Mondays at 8:30 AM**:
    
    ```
    BACKUP_INTERVAL=Weekly
    BACKUP_TIME=08:30
    ```
    
- **Monthly on the 1st at 12:00 PM**:
    
    ```
    BACKUP_INTERVAL=Monthly
    BACKUP_TIME=12:00
    ```
    

## Backblaze B2 Setup

If you're using Backblaze B2 for cloud backups, ensure you have **ALL  of** the following or you will see errors:

- **Application Key ID**: This is the `KeyID` from your Backblaze B2 application key.
- **Application Key**: The secret key associated with your application key.
- **Bucket Name**: The name of the bucket where backups will be uploaded.
- **Endpoint URL**: The S3-compatible endpoint URL for your bucket's region.

## Notes

- **Timezones**: The application uses the timezone specified in the `TIMEZONE` variable to schedule backups at the correct local time.
    
- **Logging**: The application logs its activities. When running in Docker, you can view logs using `docker compose logs -f`.

## Troubleshooting

- **Invalid Notion API Token**: If you receive authentication errors, ensure your `NOTION_API_TOKEN` is correct and that the integration has access to the pages/databases you're trying to back up.
- **Backblaze Authentication Errors**: Double-check your `B2_KEY_ID`, `B2_APPLICATION_KEY`, `B2_BUCKET_NAME`, and `B2_ENDPOINT_URL`.
- **Timezone Issues**: If backups are not running at the expected times, verify that the `TIMEZONE` variable is set correctly.
- **Invalid `BACKUP_TIME` Format**: Ensure `BACKUP_TIME` is in `HH:MM` 24-hour format, e.g., `14:30`.
- **No Backups Occurring**: Ensure at least one backup method (`local`, `backblaze`) is enabled and correctly configured.
- **Docker Permissions**: If you're experiencing permission issues with local backups in Docker, check that the Docker user has write permissions to the `EXPORT_PATH`.

## License

This project is licensed under the MIT License.
