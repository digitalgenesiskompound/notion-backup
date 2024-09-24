import os
import re
import logging
import boto3
import schedule
import time
import csv
import io
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
        elif block_type == "child_database":
            database_id = block.get("id")
            child_database = notion.databases.retrieve(database_id)
            child_title = child_database.get("title", [{}])[0].get("plain_text", "Untitled")
            markdown_content += f"### Child Database: {child_title}\n\n"
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
        logger.error(f"Error converting page {page_id} to Markdown: {e}")
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

def export_pages(items):
    """
    Export the pages and databases to files locally and/or upload to Backblaze B2.
    """
    try:
        total_items = len(items)
        logger.info(f"Starting export of {total_items} items...")

        if enable_local_backup and not os.path.exists(EXPORT_PATH):
            os.makedirs(EXPORT_PATH)

        for idx, item in enumerate(items, start=1):
            # Handle both pages and databases
            item_title = "Untitled"
            content = ''
            file_name = ''

            if item['object'] == 'database':
                item_title = item.get("title", [{}])[0].get("plain_text", "Untitled")
                sanitized_title = sanitize_filename(item_title)
                file_name = f"{sanitized_title.replace(' ', '_')}.csv"
                logger.info(f"Processing database {idx}/{total_items}: {item_title}")

                # Export the database to CSV
                content = export_database_to_csv(item)
                if not content:
                    logger.error(f"Failed to export database {item_title}")
                    continue

                # Set up directory for databases and file path
                if enable_local_backup:
                    db_export_path = os.path.join(EXPORT_PATH, "databases")
                    if not os.path.exists(db_export_path):
                        os.makedirs(db_export_path)
                    file_path = os.path.join(db_export_path, file_name)
                else:
                    file_path = None

                # For Backblaze, adjust the object key to include the directory
                backblaze_file_name = f"databases/{file_name}"

            elif item['object'] == 'page':
                item_title = get_page_title(item)
                sanitized_title = sanitize_filename(item_title)
                file_name = f"{sanitized_title.replace(' ', '_')}.md"
                logger.info(f"Processing page {idx}/{total_items}: {item_title}")

                # Export the page to Markdown
                content = page_to_markdown(item)
                if not content:
                    logger.error(f"Failed to export page {item_title}")
                    continue

                # Set up directory for pages and file path
                if enable_local_backup:
                    page_export_path = os.path.join(EXPORT_PATH, "pages")
                    if not os.path.exists(page_export_path):
                        os.makedirs(page_export_path)
                    file_path = os.path.join(page_export_path, file_name)
                else:
                    file_path = None

                # For Backblaze, adjust the object key to include the directory
                backblaze_file_name = f"pages/{file_name}"

            else:
                logger.warning(f"Unknown object type {item['object']}, skipping.")
                continue

            # Now, save or upload the content
            if enable_local_backup and file_path:
                with open(file_path, 'w', encoding="utf-8-sig", newline='') as f:
                    f.write(content)
                logger.info(f"Exported {item_title} to {file_path}")

            if enable_backblaze_backup:
                upload_to_backblaze(content, backblaze_file_name)

        logger.info("Export completed.")

    except Exception as e:
        logger.error(f"Error exporting items: {e}")



def export_database_to_csv(database):
    try:
        database_id = database['id']
        all_entries = []

        # Fetch all entries in the database
        response = notion.databases.query(database_id=database_id, page_size=100)
        all_entries.extend(response.get('results', []))

        while response.get('has_more'):
            response = notion.databases.query(
                database_id=database_id,
                start_cursor=response['next_cursor'],
                page_size=100
            )
            all_entries.extend(response.get('results', []))

        if not all_entries:
            logger.warning(f"No entries found in database {database_id}")
            return ''

        # Get the property names (column names) from the database schema
        properties = database.get('properties', {})
        headers = list(properties.keys())

        # Prepare CSV data
        output = io.StringIO()
        writer = csv.writer(output, delimiter=',', quoting=csv.QUOTE_ALL, lineterminator='\n')

        # Write headers
        writer.writerow(headers)

        # Write rows
        for entry in all_entries:
            row = []
            for header in headers:
                prop = entry.get('properties', {}).get(header, {})
                cell_value = extract_property_value(prop)
                row.append(cell_value)
            writer.writerow(row)

        csv_content = output.getvalue()
        output.close()
        return csv_content

    except Exception as e:
        logger.error(f"Error exporting database to CSV: {e}")
        return ''

def extract_property_value(prop):
    prop_type = prop.get('type')

    if prop_type in ['title', 'rich_text']:
        return get_rich_text(prop.get(prop_type, []))
    elif prop_type == 'number':
        return str(prop.get('number', ''))
    elif prop_type == 'select':
        select = prop.get('select', {})
        return select.get('name', '')
    elif prop_type == 'multi_select':
        multi_select = prop.get('multi_select', [])
        return ', '.join([item.get('name', '') for item in multi_select])
    elif prop_type == 'date':
        date = prop.get('date', {})
        return date.get('start', '')
    elif prop_type == 'checkbox':
        return str(prop.get('checkbox', False))
    elif prop_type in ['url', 'email', 'phone_number', 'created_time', 'last_edited_time']:
        return prop.get(prop_type, '')
    elif prop_type == 'people':
        people = prop.get('people', [])
        return ', '.join([person.get('name', '') for person in people])
    elif prop_type == 'files':
        files = prop.get('files', [])
        file_urls = []
        for file in files:
            file_type = file.get('type')
            file_data = file.get(file_type, {})
            url = file_data.get('url', '')
            file_urls.append(url)
        return ', '.join(file_urls)
    elif prop_type == 'formula':
        formula = prop.get('formula', {})
        formula_type = formula.get('type')
        return str(formula.get(formula_type, ''))
    elif prop_type == 'relation':
        relations = prop.get('relation', [])
        return ', '.join([rel.get('id', '') for rel in relations])
    elif prop_type == 'rollup':
        rollup = prop.get('rollup', {})
        rollup_type = rollup.get('type')
        if rollup_type == 'array':
            array = rollup.get('array', [])
            values = [extract_property_value(item) for item in array]
            return ', '.join(values)
        else:
            return str(rollup.get(rollup_type, ''))
    else:
        # Log a warning for unknown property types
        logger.warning(f"Unknown property type: {prop_type}")
        # Attempt to extract value dynamically
        value = prop.get(prop_type)
        if isinstance(value, dict):
            # Try to handle nested dictionaries
            return str(value.get('name', '')) or str(value.get('start', '')) or json.dumps(value)
        elif isinstance(value, list):
            # If it's a list, attempt to extract text
            return ', '.join([str(v) for v in value])
        elif value is not None:
            return str(value)
        else:
            return ''


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

