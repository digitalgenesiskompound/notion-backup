import os
import re
import logging
import boto3
import schedule
import time
from notion_client import Client
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# Get environment variables
NOTION_API_TOKEN = os.getenv("NOTION_API_TOKEN")
EXPORT_PATH = os.getenv("EXPORT_PATH")
BACKUP_METHODS = os.getenv("BACKUP_METHODS", "both").lower()

# Backblaze B2 credentials
B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME")
B2_ENDPOINT_URL = os.getenv("B2_ENDPOINT_URL")

# Initialize the Notion client
notion = Client(auth=NOTION_API_TOKEN)

# Configure logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

logging.getLogger('notion_client').setLevel(logging.WARNING)
logging.getLogger('http').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

enable_local_backup = False
enable_backblaze_backup = False

if BACKUP_METHODS in ['local', 'both']:
    if EXPORT_PATH:
        enable_local_backup = True
        logger.info(f"Local backup enabled. Files will be saved to {EXPORT_PATH}")
    else:
        logger.warning("Local backup requested but EXPORT_PATH is not set.")

if BACKUP_METHODS in ['backblaze', 'both']:
    if B2_KEY_ID and B2_APPLICATION_KEY and B2_BUCKET_NAME and B2_ENDPOINT_URL:
        enable_backblaze_backup = True
        logger.info("Backblaze backup enabled. Files will be uploaded to Backblaze B2.")
        # Initialize Backblaze B2 S3 client
        s3 = boto3.client(
            's3',
            endpoint_url=B2_ENDPOINT_URL,
            aws_access_key_id=B2_KEY_ID,
            aws_secret_access_key=B2_APPLICATION_KEY
        )
    else:
        logger.warning("Backblaze backup requested but credentials are not fully set.")

if not enable_local_backup and not enable_backblaze_backup:
    logger.error("No valid backup methods enabled. Please check your .env configuration.")
    exit(1)


def fetch_notion_pages_and_databases():
    pages_and_databases = []
    try:
        logger.info("Starting to fetch pages and databases...")
        response = notion.search(page_size=100)  # Fetch up to 100 items per call
        pages_and_databases.extend(response.get("results", []))
        logger.info(f"Fetched {len(pages_and_databases)} items.")

        # If there are more pages (pagination)
        while response.get("has_more"):
            response = notion.search(start_cursor=response["next_cursor"], page_size=100)
            pages_and_databases.extend(response.get("results", []))
            logger.info(f"Fetched {len(pages_and_databases)} items so far...")

    except Exception as e:
        logger.error(f"Error fetching pages and databases: {e}")

    logger.info("Finished fetching pages and databases.")
    return pages_and_databases

def retrieve_all_blocks(block_id):
    blocks = []
    try:
        response = notion.blocks.children.list(block_id=block_id, page_size=100)
        blocks.extend(response.get("results", []))

        while response.get("has_more"):
            response = notion.blocks.children.list(
                block_id=block_id,
                start_cursor=response["next_cursor"],
                page_size=100
            )
            blocks.extend(response.get("results", []))

        # Recursively retrieve children
        for block in blocks:
            if block.get("has_children"):
                child_blocks = retrieve_all_blocks(block["id"])
                block["children"] = child_blocks
    except Exception as e:
        logger.error(f"Error retrieving blocks for block_id {block_id}: {e}")

    return blocks

def get_rich_text(rich_text_array):
    text_content = ""
    for rich_text in rich_text_array:
        plain_text = rich_text.get("plain_text", "")
        annotations = rich_text.get("annotations", {})
        href = rich_text.get("href", None)

        # Apply annotations
        if annotations.get("code"):
            plain_text = f"`{plain_text}`"
        if annotations.get("bold"):
            plain_text = f"**{plain_text}**"
        if annotations.get("italic"):
            plain_text = f"*{plain_text}*"
        if annotations.get("strikethrough"):
            plain_text = f"~~{plain_text}~~"
        if annotations.get("underline"):
            plain_text = f"<u>{plain_text}</u>"

        # Handle links
        if href:
            plain_text = f"[{plain_text}]({href})"

        text_content += plain_text
    return text_content

def process_block(block):
    markdown_content = ""
    block_type = block.get("type")
    try:
        if block_type == "paragraph":
            text_content = get_rich_text(block.get("paragraph", {}).get("rich_text", []))
            markdown_content += f"{text_content}\n\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []))
                markdown_content += child_markdown
        elif block_type == "heading_1":
            text_content = get_rich_text(block.get("heading_1", {}).get("rich_text", []))
            markdown_content += f"# {text_content}\n\n"
        elif block_type == "heading_2":
            text_content = get_rich_text(block.get("heading_2", {}).get("rich_text", []))
            markdown_content += f"## {text_content}\n\n"
        elif block_type == "heading_3":
            text_content = get_rich_text(block.get("heading_3", {}).get("rich_text", []))
            markdown_content += f"### {text_content}\n\n"
        elif block_type == "bulleted_list_item":
            text_content = get_rich_text(block.get("bulleted_list_item", {}).get("rich_text", []))
            markdown_content += f"- {text_content}\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []))
                markdown_content += child_markdown
        elif block_type == "numbered_list_item":
            text_content = get_rich_text(block.get("numbered_list_item", {}).get("rich_text", []))
            markdown_content += f"1. {text_content}\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []))
                markdown_content += child_markdown
        elif block_type == "to_do":
            text_content = get_rich_text(block.get("to_do", {}).get("rich_text", []))
            checked = block.get("to_do", {}).get("checked")
            checkbox = "[x]" if checked else "[ ]"
            markdown_content += f"{checkbox} {text_content}\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []))
                markdown_content += child_markdown
        elif block_type == "toggle":
            text_content = get_rich_text(block.get("toggle", {}).get("rich_text", []))
            markdown_content += f"<details><summary>{text_content}</summary>\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []))
                markdown_content += child_markdown
            markdown_content += "</details>\n"
        elif block_type == "quote":
            text_content = get_rich_text(block.get("quote", {}).get("rich_text", []))
            markdown_content += f"> {text_content}\n\n"
        elif block_type == "code":
            language = block.get("code", {}).get("language", "")
            text_content = get_rich_text(block.get("code", {}).get("rich_text", []))

            # If language is 'plain text' or empty, omit it
            if language.lower() == "plain text" or not language:
                markdown_content += f"```\n{text_content}\n```\n"
            else:
                markdown_content += f"```{language}\n{text_content}\n```\n"
        elif block_type == "divider":
            markdown_content += "---\n\n"
        elif block_type == "image":
            image_type = block.get("image", {}).get("type")
            if image_type == "file":
                image_url = block.get("image", {}).get("file", {}).get("url", "")
            elif image_type == "external":
                image_url = block.get("image", {}).get("external", {}).get("url", "")
            caption = get_rich_text(block.get("image", {}).get("caption", []))
            markdown_content += f"![{caption}]({image_url})\n\n"
        elif block_type == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            markdown_content += f"[Bookmark]({url})\n\n"
        elif block_type == "child_page":
            page_id = block.get("id")
            child_page = notion.pages.retrieve(page_id)
            child_title = get_page_title(child_page)
            markdown_content += f"## {child_title}\n\n"
            # Include content from the child page
            child_blocks = retrieve_all_blocks(page_id)
            child_content = blocks_to_markdown(child_blocks)
            markdown_content += child_content
        elif block_type == "column_list":
            # Process each column
            if block.get("has_children"):
                for column in block.get("children", []):
                    column_content = process_block(column)
                    markdown_content += column_content
        elif block_type == "column":
            # Process blocks inside the column
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []))
                markdown_content += child_markdown
        else:
            logger.warning(f"Unsupported block type: {block_type}")
    except Exception as e:
        logger.error(f"Error processing block {block_type}: {e}")
    return markdown_content

def blocks_to_markdown(blocks):
    markdown_content = ""
    for block in blocks:
        content = process_block(block)
        markdown_content += content
    return markdown_content

def page_to_markdown(page):
    markdown_content = ""
    try:
        page_id = page['id']
        blocks = retrieve_all_blocks(page_id)
        markdown_content = blocks_to_markdown(blocks)
    except Exception as e:
        logger.error(f"Error converting page to Markdown: {e}")
    return markdown_content

def get_page_title(page):
    try:
        # Get all properties
        properties = page.get("properties", {})
        # Loop through properties to find the title property
        for prop_name, prop in properties.items():
            if prop.get("type") == "title":
                title_array = prop.get("title", [])
                if title_array:
                    title_text = get_rich_text(title_array)
                    return title_text
        # If no title property found, return "Untitled"
        return "Untitled"
    except Exception as e:
        logger.error(f"Error getting page title: {e}")
        return "Untitled"

def sanitize_filename(filename):
    # Remove Markdown formatting and invalid filename characters
    filename = re.sub(r'[*_~`<>:"/\\|?*]', '', filename)
    return filename.strip()

def upload_to_backblaze(content, file_name):
    try:
        s3.put_object(Bucket=B2_BUCKET_NAME, Key=file_name, Body=content.encode('utf-8'))
        logger.info(f"Uploaded {file_name} to Backblaze B2 bucket {B2_BUCKET_NAME}")
    except Exception as e:
        logger.error(f"Error uploading {file_name} to Backblaze B2: {e}")

def export_pages(pages):
    """
    Export the pages to .md files locally and/or upload to Backblaze B2.
    """
    try:
        total_pages = len(pages)
        logger.info(f"Starting export of {total_pages} pages...")

        if enable_local_backup and not os.path.exists(EXPORT_PATH):
            os.makedirs(EXPORT_PATH)

        for idx, page in enumerate(pages, start=1):
            # Handle both pages and databases
            page_title = "Untitled"
            if page['object'] == 'database':
                page_title = page.get("title", [{}])[0].get("plain_text", "Untitled")
            elif page['object'] == 'page':
                page_title = get_page_title(page)

            sanitized_title = sanitize_filename(page_title)
            file_name = f"{sanitized_title.replace(' ', '_')}.md"

            logger.info(f"Processing page {idx}/{total_pages}: {page_title}")

            markdown_content = page_to_markdown(page)

            if enable_local_backup:
                file_path = os.path.join(EXPORT_PATH, file_name)
                with open(file_path, 'w', encoding="utf-8") as f:
                    f.write(markdown_content)
                logger.info(f"Exported {page_title} to {file_path}")

            if enable_backblaze_backup:
                upload_to_backblaze(markdown_content, file_name)

        logger.info("Export completed.")

    except Exception as e:
        logger.error(f"Error exporting pages: {e}")

def main_backup():
    logger.info("Starting backup process...")
    pages = fetch_notion_pages_and_databases()

    if pages:
        logger.info("Exporting pages and databases...")
        export_pages(pages)
    else:
        logger.warning("No pages or databases found.")

def schedule_backup():
    interval = os.getenv("BACKUP_INTERVAL", "Daily").lower()
    backup_time = os.getenv("BACKUP_TIME", "00:00")
    try:
        # Validate backup_time format
        time.strptime(backup_time, "%H:%M")
    except ValueError:
        logger.error(f"Invalid BACKUP_TIME format: {backup_time}. Expected HH:MM in 24-hour format.")
        exit(1)

    if interval == 'hourly':
        schedule.every().hour.at(f":{backup_time.split(':')[1]}").do(main_backup)
        logger.info(f"Backup scheduled to run every hour at minute {backup_time.split(':')[1]}.")
    elif interval == 'daily':
        schedule.every().day.at(backup_time).do(main_backup)
        logger.info(f"Backup scheduled to run daily at {backup_time}.")
    elif interval == 'weekly':
        schedule.every().monday.at(backup_time).do(main_backup)
        logger.info(f"Backup scheduled to run every week on Monday at {backup_time}.")
    elif interval == 'monthly':
        schedule.every(28).days.at(backup_time).do(main_backup)
        logger.info(f"Backup scheduled to run every 28 days at {backup_time}.")
    else:
        logger.error(f"Invalid BACKUP_INTERVAL: {interval}. Defaulting to daily backup.")
        schedule.every().day.at(backup_time).do(main_backup)


if __name__ == "__main__":
    schedule_backup()
    logger.info("Scheduler initialized. Waiting for scheduled backups...")
    while True:
        schedule.run_pending()
        time.sleep(1)

