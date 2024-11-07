import os
import re
import logging
import boto3
import schedule
import time
import csv
import io
import argparse
import json
import requests
from notion_client import Client
from dotenv import load_dotenv
import traceback
import functools
from concurrent.futures import ThreadPoolExecutor
import threading

from sqlalchemy import create_engine, Column, String
from sqlalchemy.orm import declarative_base, sessionmaker

# Load environment variables from the .env file
load_dotenv()

# Get environment variables
NOTION_API_TOKEN = os.getenv("NOTION_API_TOKEN")
EXPORT_PATH = os.getenv("CONTAINER_EXPORT_PATH")
BACKUP_METHODS = os.getenv("BACKUP_METHODS", "both").lower()
HOST_EXPORT_PATH = os.getenv("HOST_EXPORT_PATH")
ROOT_DIR_NAME = os.getenv("ROOT_DIR_NAME", "pages")  # ROOT_DIR_NAME variable

# Backblaze B2 credentials
B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME")
B2_ENDPOINT_URL = os.getenv("B2_ENDPOINT_URL")

# Database configuration from environment variables
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

# SQLAlchemy setup
DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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
    if HOST_EXPORT_PATH:
        enable_local_backup = True
        logger.info(f"Local backup enabled. Files will be saved to {HOST_EXPORT_PATH}")
    else:
        logger.warning("Local backup requested but HOST_EXPORT_PATH is not set.")

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

# Create a global ThreadPoolExecutor
executor = ThreadPoolExecutor(max_workers=5)  # Adjust the number of workers as needed

# Create a threading lock for database operations
db_session = threading.local()

def get_db():
    if not hasattr(db_session, 'session'):
        db_session.session = SessionLocal()
    return db_session.session

class PageMap(Base):
    __tablename__ = "page_map"
    page_id = Column(String, primary_key=True, index=True)
    relative_path = Column(String)

def timing(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        logger.info(f"Function '{func.__name__}' executed in {end_time - start_time:.2f} seconds.")
        return result
    return wrapper

def initialize_db():
    Base.metadata.create_all(bind=engine)
    logger.info("Initialized PostgreSQL database for page mapping.")

def get_relative_path(page_id):
    db = get_db()
    page_map = db.query(PageMap).filter(PageMap.page_id == page_id).first()
    return page_map.relative_path if page_map else None

def update_relative_path(page_id, relative_path):
    db = get_db()
    page_map = db.query(PageMap).filter(PageMap.page_id == page_id).first()
    if page_map:
        page_map.relative_path = relative_path
    else:
        page_map = PageMap(page_id=page_id, relative_path=relative_path)
        db.add(page_map)
    db.commit()

def close_db():
    db = get_db()
    db.close()
    logger.info("Closed database session.")

def get_unique_directory_name(parent_path, base_name):
    directory_name = base_name
    count = 1
    while os.path.exists(os.path.join(parent_path, directory_name)):
        directory_name = f"{base_name} ({count})"
        count += 1
    return directory_name

def get_page_title(page):
    try:
        if page.get('object') == 'database':
            title_array = page.get('title', [])
            if title_array:
                title_text = get_rich_text(title_array)
                return title_text
            else:
                return "Untitled"
        elif 'properties' in page:
            properties = page.get("properties", {})
            for prop_name, prop in properties.items():
                if prop.get("type") == "title":
                    title_array = prop.get("title", [])
                    if title_array:
                        title_text = get_rich_text(title_array)
                        return title_text
        elif 'child_page' in page:
            title_text = page.get('child_page', {}).get('title', 'Untitled')
            return title_text
        # If no title property found, return "Untitled"
        return "Untitled"
    except Exception as e:
        logger.error(f"Error getting page title: {e}")
        traceback.print_exc()
        return "Untitled"

def sanitize_filename(filename):
    # Remove invalid filename characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    return filename.strip()

@timing
def fetch_notion_pages_and_databases():
    pages_and_databases = []
    try:
        logger.info("Starting to fetch top-level pages and databases...")

        # Fetch top-level pages
        has_more = True
        next_cursor = None
        while has_more:
            response = notion.search(
                filter={"value": "page", "property": "object"},
                start_cursor=next_cursor,
                page_size=100
            )
            results = response.get("results", [])
            for item in results:
                parent = item.get('parent', {})
                parent_type = parent.get('type')
                if parent_type == 'workspace':
                    pages_and_databases.append(item)
                else:
                    continue  # Skip pages that are not top-level
            has_more = response.get('has_more', False)
            next_cursor = response.get('next_cursor')
            logger.info(f"Fetched {len(pages_and_databases)} top-level pages so far...")

        # Fetch top-level databases
        has_more = True
        next_cursor = None
        while has_more:
            response = notion.search(
                filter={"value": "database", "property": "object"},
                start_cursor=next_cursor,
                page_size=100
            )
            results = response.get("results", [])
            for db in results:
                parent = db.get('parent', {})
                parent_type = parent.get('type')
                if parent_type == 'workspace':
                    database_id = db['id']
                    # Retrieve full database object
                    full_db = notion.databases.retrieve(database_id)
                    pages_and_databases.append(full_db)
                else:
                    continue  # Skip databases that are not top-level
            has_more = response.get('has_more', False)
            next_cursor = response.get('next_cursor')
            logger.info(f"Fetched {len(pages_and_databases)} top-level pages and databases so far...")

    except Exception as e:
        logger.error(f"Error fetching pages and databases: {e}")

    logger.info("Finished fetching top-level pages and databases.")
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

def process_block(block, page_export_path):
    markdown_content = ""
    block_type = block.get("type")
    try:
        if block_type == "paragraph":
            text_content = get_rich_text(block.get("paragraph", {}).get("rich_text", []))
            markdown_content += f"{text_content}\n\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []), page_export_path)
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
                child_markdown = blocks_to_markdown(block.get("children", []), page_export_path)
                markdown_content += child_markdown
        elif block_type == "numbered_list_item":
            text_content = get_rich_text(block.get("numbered_list_item", {}).get("rich_text", []))
            markdown_content += f"1. {text_content}\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []), page_export_path)
                markdown_content += child_markdown
        elif block_type == "to_do":
            text_content = get_rich_text(block.get("to_do", {}).get("rich_text", []))
            checked = block.get("to_do", {}).get("checked")
            checkbox = "[x]" if checked else "[ ]"
            markdown_content += f"{checkbox} {text_content}\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []), page_export_path)
                markdown_content += child_markdown
        elif block_type == "toggle":
            text_content = get_rich_text(block.get("toggle", {}).get("rich_text", []))
            markdown_content += f"<details><summary>{text_content}</summary>\n"
            if block.get("has_children"):
                child_markdown = blocks_to_markdown(block.get("children", []), page_export_path)
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
        elif block_type == "bookmark":
            url = block.get("bookmark", {}).get("url", "")
            markdown_content += f"[Bookmark]({url})\n\n"
        elif block_type == "child_page":
            page_id = block.get("id")
            child_title = get_page_title(block)
            sanitized_title = sanitize_filename(child_title)
            # Get the export path of the child page
            child_relative_path = get_relative_path(page_id)
            if child_relative_path:
                child_page_export_path = os.path.join(EXPORT_PATH, child_relative_path)
                relative_path = os.path.relpath(child_page_export_path, page_export_path)
                link_path = os.path.join(relative_path, f"{sanitized_title}.md")
            else:
                # If not exported yet, assume default path
                link_path = os.path.join(sanitized_title, f"{sanitized_title}.md")
            markdown_content += f"[{child_title}]({link_path})\n\n"
        elif block_type == "child_database":
            database_id = block.get("id")
            child_database = notion.databases.retrieve(database_id)
            child_title = child_database.get("title", [{}])[0].get("plain_text", "Untitled")
            sanitized_title = sanitize_filename(child_title)
            # Get the export path of the child database
            child_relative_path = get_relative_path(database_id)
            if child_relative_path:
                child_db_export_path = os.path.join(EXPORT_PATH, child_relative_path)
                relative_path = os.path.relpath(child_db_export_path, page_export_path)
                link_path = os.path.join(relative_path, f"{sanitized_title}.csv")
            else:
                # If not exported yet, assume default path
                link_path = os.path.join(sanitized_title, f"{sanitized_title}.csv")
            markdown_content += f"[{child_title} Database]({link_path})\n\n"
        elif block_type == "image":
            image_type = block.get("image", {}).get("type")
            if image_type == "file":
                image_url = block.get("image", {}).get("file", {}).get("url", "")
            elif image_type == "external":
                image_url = block.get("image", {}).get("external", {}).get("url", "")
            caption = get_rich_text(block.get("image", {}).get("caption", []))

            if enable_local_backup and page_export_path is not None:
                # Create an images directory within the page export path
                images_dir = os.path.join(page_export_path, "images")
                if not os.path.exists(images_dir):
                    os.makedirs(images_dir)
                # Generate a filename for the image
                image_filename = os.path.join(images_dir, os.path.basename(image_url.split("?")[0]))
                # Schedule the image download
                executor.submit(download_file_if_needed, image_url, image_filename)
                # Adjust the markdown to reference the local image file
                relative_path = os.path.relpath(image_filename, page_export_path)
                markdown_content += f"![{caption}]({relative_path})\n\n"
            else:
                # Use the image URL
                markdown_content += f"![{caption}]({image_url})\n\n"
        elif block_type == "file":
            file_type = block.get("file", {}).get("type")
            if file_type == "file":
                file_url = block.get("file", {}).get("file", {}).get("url", "")
            elif file_type == "external":
                file_url = block.get("file", {}).get("external", {}).get("url", "")
            caption = get_rich_text(block.get("file", {}).get("caption", []))

            if enable_local_backup and page_export_path is not None:
                # Create a files directory within the page export path
                files_dir = os.path.join(page_export_path, "files")
                if not os.path.exists(files_dir):
                    os.makedirs(files_dir)
                # Generate a filename for the file
                file_filename = os.path.join(files_dir, os.path.basename(file_url.split("?")[0]))
                # Schedule the file download
                executor.submit(download_file_if_needed, file_url, file_filename)
                # Adjust the markdown to reference the local file
                relative_path = os.path.relpath(file_filename, page_export_path)
                markdown_content += f"[{caption or 'File'}]({relative_path})\n\n"
            else:
                # Use the file URL
                markdown_content += f"[{caption or 'File'}]({file_url})\n\n"
        elif block_type == "pdf":
            pdf_type = block.get("pdf", {}).get("type")
            if pdf_type == "file":
                pdf_url = block.get("pdf", {}).get("file", {}).get("url", "")
            elif pdf_type == "external":
                pdf_url = block.get("pdf", {}).get("external", {}).get("url", "")
            caption = get_rich_text(block.get("pdf", {}).get("caption", []))

            if enable_local_backup and page_export_path is not None:
                # Create a files directory within the page export path
                files_dir = os.path.join(page_export_path, "files")
                if not os.path.exists(files_dir):
                    os.makedirs(files_dir)
                # Generate a filename for the PDF
                pdf_filename = os.path.join(files_dir, os.path.basename(pdf_url.split("?")[0]))
                # Schedule the PDF download
                executor.submit(download_file_if_needed, pdf_url, pdf_filename)
                # Adjust the markdown to reference the local PDF file
                relative_path = os.path.relpath(pdf_filename, page_export_path)
                markdown_content += f"[{caption or 'PDF'}]({relative_path})\n\n"
            else:
                # Use the PDF URL
                markdown_content += f"[{caption or 'PDF'}]({pdf_url})\n\n"
        elif block_type == "callout":
            text_content = get_rich_text(block.get("callout", {}).get("rich_text", []))
            icon = block.get("callout", {}).get("icon", {})
            if icon:
                if icon.get("type") == "emoji":
                    emoji = icon.get("emoji", "")
                    text_content = f"{emoji} {text_content}"
            # Style callouts as blockquotes or custom format
            markdown_content += f"> **Callout:** {text_content}\n\n"
        elif block_type == "table":
            table_id = block.get("id")
            table_rows = retrieve_all_blocks(table_id)
            if table_rows:
                # Extract table data
                table_data = []
                for row in table_rows:
                    if row.get('type') == 'table_row':
                        cells = row.get('table_row', {}).get('cells', [])
                        row_data = [get_rich_text(cell) for cell in cells]
                        table_data.append(row_data)
                # Save the table as CSV
                csv_file_name = f"table_{table_id}.csv"
                csv_file_path = os.path.join(page_export_path, csv_file_name)
                save_csv_if_needed(table_data, csv_file_path)
                logger.info(f"Exported table to {csv_file_path}")
                # Add a link to the CSV file in the Markdown content
                markdown_content += f"[View Table]({csv_file_name})\n\n"
            else:
                logger.warning(f"No rows found for table {table_id}")
        else:
            logger.warning(f"Unsupported block type: {block_type}")
    except Exception as e:
        logger.error(f"Error processing block {block_type}: {e}")
        traceback.print_exc()
    return markdown_content

def download_file_if_needed(file_url, file_path):
    try:
        if os.path.exists(file_path):
            logger.info(f"File already exists: {file_path}. Skipping download.")
            return
        response = requests.get(file_url)
        if response.status_code == 200:
            with open(file_path, 'wb') as file:
                file.write(response.content)
            logger.info(f"Saved file to {file_path}")
        else:
            logger.warning(f"Failed to download file from {file_url}")
    except Exception as e:
        logger.error(f"Error downloading file {file_url}: {e}")

def save_csv_if_needed(table_data, csv_file_path):
    try:
        csv_content = io.StringIO()
        writer = csv.writer(csv_content, delimiter=',', quoting=csv.QUOTE_ALL, lineterminator='\n')
        writer.writerows(table_data)
        new_content = csv_content.getvalue()
        csv_content.close()

        # Check if file exists and content is the same
        if os.path.exists(csv_file_path):
            with open(csv_file_path, 'r', encoding='utf-8-sig') as f:
                existing_content = f.read()
            if existing_content == new_content:
                logger.info(f"No changes detected in {csv_file_path}. Skipping save.")
                return

        # Save new content
        with open(csv_file_path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(new_content)
        logger.info(f"Saved CSV to {csv_file_path}")

    except Exception as e:
        logger.error(f"Error saving CSV {csv_file_path}: {e}")

def blocks_to_markdown(blocks, page_export_path):
    markdown_content = ""
    for block in blocks:
        content = process_block(block, page_export_path)
        markdown_content += content
    return markdown_content

def page_to_markdown(page, page_export_path):
    markdown_content = ""
    try:
        page_id = page['id']
        blocks = retrieve_all_blocks(page_id)
        markdown_content = blocks_to_markdown(blocks, page_export_path)
    except Exception as e:
        logger.error(f"Error converting page {page_id} to Markdown: {e}")
        traceback.print_exc()
    return markdown_content

def is_content_same(new_content, file_path):
    with open(file_path, 'r', encoding="utf-8-sig") as f:
        existing_content = f.read()
    return new_content == existing_content

def get_database_entries(database_id):
    entries = []
    try:
        response = notion.databases.query(database_id=database_id, page_size=100)
        results = response.get('results', [])
        while True:
            for entry in results:
                page_id = entry['id']
                # Retrieve full page data
                full_page = notion.pages.retrieve(page_id)
                entries.append(full_page)
            if not response.get('has_more'):
                break
            response = notion.databases.query(
                database_id=database_id,
                start_cursor=response['next_cursor'],
                page_size=100
            )
            results = response.get('results', [])
    except Exception as e:
        logger.error(f"Error fetching entries for database {database_id}: {e}")
    return entries


@timing
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
        traceback.print_exc()
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
    elif prop_type == 'status':
        status = prop.get('status', {})
        return status.get('name', '')
    elif prop_type == 'button':
        # Buttons don't have a straightforward representation in Markdown
        # You can choose to include the button text or ignore it
        return '[Button]'
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

def get_child_pages(page_id):
    child_items = []
    try:
        blocks = notion.blocks.children.list(block_id=page_id, page_size=100)
        while True:
            results = blocks.get('results', [])
            for block in results:
                block_type = block.get('type')
                if block_type == 'child_page':
                    child_page_id = block['id']
                    child_page = notion.pages.retrieve(child_page_id)
                    child_items.append(child_page)
                elif block_type == 'child_database':
                    child_database_id = block['id']
                    # Retrieve full database object
                    child_database = notion.databases.retrieve(child_database_id)
                    child_items.append(child_database)
            if not blocks.get('has_more'):
                break
            blocks = notion.blocks.children.list(
                block_id=page_id,
                start_cursor=blocks['next_cursor'],
                page_size=100
            )
    except Exception as e:
        logger.error(f"Error fetching child pages and databases for page {page_id}: {e}")
    return child_items

@timing
def export_pages(items, parent_path=""):
    try:
        total_items = len(items)
        logger.info(f"Starting export of {total_items} items...")

        if enable_local_backup and not os.path.exists(EXPORT_PATH):
            os.makedirs(EXPORT_PATH)

        for idx, item in enumerate(items, start=1):
            item_id = item['id']
            item_title = get_page_title(item)
            sanitized_title = sanitize_filename(item_title)

            # Determine the export path
            relative_path = get_relative_path(item_id)
            if relative_path:
                item_export_path = os.path.join(EXPORT_PATH, relative_path)
                # Check if title has changed
                current_dir_name = os.path.basename(item_export_path)
                if current_dir_name != sanitized_title:
                    # Rename the directory
                    new_item_export_path = os.path.join(os.path.dirname(item_export_path), sanitized_title)
                    os.rename(item_export_path, new_item_export_path)
                    item_export_path = new_item_export_path
                    new_relative_path = os.path.relpath(item_export_path, EXPORT_PATH)
                    update_relative_path(item_id, new_relative_path)
                    logger.info(f"Renamed directory to {new_item_export_path}")
                # Ensure the directory exists
                if not os.path.exists(item_export_path):
                    os.makedirs(item_export_path)
            else:
                # Create a new directory
                if parent_path:
                    base_path = parent_path
                    relative_base_path = os.path.relpath(base_path, EXPORT_PATH)
                else:
                    base_path = os.path.join(EXPORT_PATH, ROOT_DIR_NAME)
                    relative_base_path = os.path.relpath(base_path, EXPORT_PATH)
                    if not os.path.exists(base_path):
                        os.makedirs(base_path)
                # Handle name collisions
                directory_name = get_unique_directory_name(base_path, sanitized_title)
                item_export_path = os.path.join(base_path, directory_name)
                relative_path = os.path.join(relative_base_path, directory_name)
                update_relative_path(item_id, relative_path)
                if not os.path.exists(item_export_path):
                    os.makedirs(item_export_path)

            if item['object'] == 'database':
                file_name = f"{sanitized_title}.csv"
            elif item['object'] == 'page':
                file_name = f"{sanitized_title}.md"
            else:
                logger.warning(f"Unknown object type: {item['object']}")
                continue

            file_path = os.path.join(item_export_path, file_name)

            logger.info(f"Processing {item['object']} {idx}/{total_items}: {item_title}")

            # Export content
            if item['object'] == 'database':
                content = export_database_to_csv(item)
                # Now retrieve pages within the database
                database_entries = get_database_entries(item_id)
                if database_entries:
                    export_pages(database_entries, parent_path=item_export_path)
            elif item['object'] == 'page':
                content = page_to_markdown(item, item_export_path)
            else:
                content = ''

            if not content:
                logger.error(f"Failed to export {item_title}")
                continue

            backblaze_file_name = os.path.join(relative_path, file_name)

            # Now, save or upload the content
            if enable_local_backup and file_path:
                # Check if file exists and content has changed
                if not os.path.exists(file_path) or not is_content_same(content, file_path):
                    with open(file_path, 'w', encoding="utf-8-sig", newline='') as f:
                        f.write(content)
                    logger.info(f"Exported {item_title} to {file_path}")
                else:
                    logger.info(f"No changes detected in {file_path}. Skipping save.")

            if enable_backblaze_backup:
                upload_to_backblaze(content, backblaze_file_name)

            # Recursively export child items (pages and databases)
            child_items = get_child_pages(item_id)
            if child_items:
                export_pages(child_items, parent_path=item_export_path)

        logger.info("Export completed.")

    except Exception as e:
        logger.error(f"Error exporting items: {e}")
        traceback.print_exc()


def upload_to_backblaze(content, file_name):
    try:
        s3.put_object(Bucket=B2_BUCKET_NAME, Key=file_name, Body=content.encode('utf-8'))
        logger.info(f"Uploaded {file_name} to Backblaze B2 bucket {B2_BUCKET_NAME}")
    except Exception as e:
        logger.error(f"Error uploading {file_name} to Backblaze B2: {e}")

@timing
def main_backup():
    logger.info("Starting backup process...")
    initialize_db()
    pages = fetch_notion_pages_and_databases()

    if pages:
        logger.info("Exporting pages and databases...")
        export_pages(pages)
    else:
        logger.warning("No pages or databases found.")

    executor.shutdown(wait=True)  # Wait for all downloads to complete
    close_db()  # Optional since connections are closed automatically

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
    parser = argparse.ArgumentParser(description='Notion Backup Script')
    parser.add_argument('--run-now', action='store_true', help='Run backup immediately')
    args = parser.parse_args()

    if args.run_now:
        main_backup()
    else:
        schedule_backup()
        logger.info("Scheduler initialized. Waiting for scheduled backups...")
        while True:
            schedule.run_pending()
            time.sleep(1)
