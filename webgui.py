import logging
from flask import Flask, send_file, send_from_directory, abort, render_template, request, Response, jsonify
from flask_cors import CORS
import os
import zipfile
import io
import datetime
from werkzeug.utils import secure_filename
import shutil

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKUP_DIR = "/notion-backup"
BASE_BACKUP_DIR = os.path.join(BACKUP_DIR, 'backup')  # Define base backup directory

USERNAME = "username"  # Replace with actual logic to obtain username if needed.

# Configure upload parameters
UPLOAD_EXTENSIONS = set(['.txt', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.docx', '.xlsx', '.pptx', '.zip'])  # Define allowed extensions
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB limit per file

def secure_path(path):
    # Ensure the path is secure and within BASE_BACKUP_DIR
    abs_path = os.path.abspath(os.path.join(BASE_BACKUP_DIR, path))
    if not abs_path.startswith(os.path.abspath(BASE_BACKUP_DIR)):
        logger.warning(f"Attempted access to forbidden path: {path}")
        abort(403)  # Forbidden
    return abs_path

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/list', methods=['GET'])
def list_directory():
    path = request.args.get('path', '')
    logger.info(f"Listing directory: {path}")
    try:
        current_dir = secure_path(path)
        if not os.path.exists(current_dir):
            logger.error(f"Directory not found: {current_dir}")
            return jsonify({'error': 'Directory not found'}), 404

        directories = []
        files = []
        for item in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item)
            if os.path.isdir(item_path):
                directories.append(item)
            elif os.path.isfile(item_path):
                stat = os.stat(item_path)
                files.append({
                    'name': item,
                    'size': stat.st_size,
                    'lastModified': int(stat.st_mtime),  # Unix timestamp in seconds
                    'path': os.path.relpath(item_path, BASE_BACKUP_DIR)
                })

        directories.sort()
        files.sort(key=lambda x: x['name'].lower())

        # Build breadcrumb data
        breadcrumb = []
        if path:
            parts = path.strip('/').split('/')
            accumulated_path = ''
            breadcrumb.append({'name': 'Root', 'path': ''})
            for part in parts:
                accumulated_path = os.path.join(accumulated_path, part)
                breadcrumb.append({'name': part, 'path': accumulated_path})
        else:
            breadcrumb.append({'name': 'Root', 'path': ''})

        return jsonify({
            'directories': directories,
            'files': files,
            'breadcrumb': breadcrumb
        })

    except Exception as e:
        logger.exception(f"Error listing directory {path}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/search', methods=['GET'])
def search():
    query = request.args.get('query', '').lower()
    if not query:
        logger.error("No search query provided.")
        return jsonify({'error': 'No search query provided.'}), 400

    logger.info(f"Performing search for query: {query}")
    matched_directories = []
    matched_files = []

    for root, dirs, files in os.walk(BASE_BACKUP_DIR):
        # Check directories
        for dir in dirs:
            if query in dir.lower():
                relative_path = os.path.relpath(os.path.join(root, dir), BASE_BACKUP_DIR)
                matched_directories.append(relative_path)

        # Check files
        for file in files:
            if query in file.lower():
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, BASE_BACKUP_DIR)
                stat = os.stat(file_path)
                matched_files.append({
                    'name': file,
                    'size': stat.st_size,
                    'lastModified': int(stat.st_mtime),  # Unix timestamp in seconds
                    'path': relative_path
                })

    # Build breadcrumb for search results
    breadcrumb = [{'name': 'Root', 'path': ''}, {'name': f"Search Results for '{query}'", 'path': ''}]

    logger.info(f"Search found {len(matched_directories)} directories and {len(matched_files)} files.")
    return jsonify({
        'directories': matched_directories,
        'files': matched_files,
        'breadcrumb': breadcrumb
    })

@app.route('/upload', methods=['POST'])
def upload_files():
    try:
        # Get the target path
        path = request.form.get('path', '')
        target_dir = secure_path(path)

        logger.info(f"Uploading files to: {path}")

        # Ensure the target directory exists
        if not os.path.exists(target_dir):
            logger.error(f"Target directory does not exist: {target_dir}")
            return jsonify({'error': 'Target directory does not exist.'}), 400

        uploaded_files = request.files.getlist('files')
        if not uploaded_files:
            logger.error("No files uploaded.")
            return jsonify({'error': 'No files uploaded.'}), 400

        for file in uploaded_files:
            filename = secure_filename(file.filename)
            if filename != '':
                file_ext = os.path.splitext(filename)[1].lower()
                if file_ext not in UPLOAD_EXTENSIONS:
                    logger.error(f"File extension {file_ext} is not allowed for file {filename}.")
                    return jsonify({'error': f'File extension {file_ext} is not allowed.'}), 400
                file_path = os.path.join(target_dir, filename)
                # Prevent overwriting existing files
                if os.path.exists(file_path):
                    logger.error(f'File "{filename}" already exists in {path}.')
                    return jsonify({'error': f'File "{filename}" already exists.'}), 400
                file.save(file_path)
                logger.info(f"Uploaded file: {file_path}")

        return jsonify({'message': 'Files uploaded successfully.'}), 200

    except Exception as e:
        logger.exception(f"Error uploading files to {path}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete', methods=['POST'])
def delete_item():
    try:
        data = request.get_json()
        paths = data.get('path', [])
        if not paths:
            logger.error("No paths provided for deletion.")
            return jsonify({'error': 'No paths provided for deletion.'}), 400

        if not isinstance(paths, list):
            paths = [paths]

        deleted = []
        errors = []

        for path in paths:
            logger.info(f"Attempting to delete: {path}")
            target_path = secure_path(path)

            if not os.path.exists(target_path):
                logger.error(f"Item does not exist: {target_path}")
                errors.append({'path': path, 'error': 'Item does not exist.'})
                continue

            try:
                if os.path.isfile(target_path):
                    os.remove(target_path)
                    logger.info(f"Deleted file: {target_path}")
                elif os.path.isdir(target_path):
                    shutil.rmtree(target_path)  # Removes directory and all its contents
                    logger.info(f"Deleted directory and its contents: {target_path}")
                else:
                    logger.error(f"Selected path is neither a file nor a directory: {target_path}")
                    errors.append({'path': path, 'error': 'Neither file nor directory.'})
                    continue
                deleted.append(path)
            except Exception as e:
                logger.exception(f"Error deleting item {path}: {e}")
                errors.append({'path': path, 'error': str(e)})

        if errors:
            return jsonify({'message': 'Some items were not deleted.', 'deleted': deleted, 'errors': errors}), 207  # Multi-Status
        else:
            return jsonify({'message': 'All items deleted successfully.', 'deleted': deleted}), 200

    except Exception as e:
        logger.exception(f"Error deleting items: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/download_all')
def download_all():
    try:
        if not os.path.exists(BASE_BACKUP_DIR):
            logger.error("Backup directory not found.")
            return jsonify({'error': 'Backup directory not found.'}), 500

        logger.info("Creating ZIP for all backups.")

        # Create a zip file in-memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, _, files in os.walk(BASE_BACKUP_DIR):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Add the file to the zip, preserving its relative path
                    relative_path = os.path.relpath(file_path, BASE_BACKUP_DIR)
                    zip_file.write(file_path, relative_path)
                    logger.debug(f"Added to ZIP: {relative_path}")

        # Set the pointer to the start of the BytesIO buffer
        zip_buffer.seek(0)

        # Serve the zip file as a downloadable response with a custom name
        zip_filename = f"notionbackup-{USERNAME}-all.zip"
        logger.info(f"Serving ZIP file: {zip_filename}")
        return Response(
            zip_buffer,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename={zip_filename}'
            }
        )

    except Exception as e:
        logger.exception(f"Error creating ZIP: {e}")
        return jsonify({'error': f"An error occurred while creating ZIP: {str(e)}"}), 500

@app.route('/download_selected', methods=['POST'])
def download_selected():
    try:
        # Get the list of selected paths from the JSON payload
        selected_paths = request.json.get('selected_paths', [])

        if not selected_paths:
            logger.error("No files or directories selected for download.")
            return jsonify({'error': 'No files or directories selected for download.'}), 400

        # Secure and validate all selected paths
        absolute_paths = [secure_path(path) for path in selected_paths]

        logger.info(f"Creating ZIP for selected items: {selected_paths}")

        # If only one file or directory is selected, handle accordingly
        if len(absolute_paths) == 1:
            selected_path = absolute_paths[0]
            relative_path = os.path.relpath(selected_path, BASE_BACKUP_DIR)

            if os.path.isfile(selected_path):
                # Serve the single file
                logger.info(f"Serving single file: {selected_path}")
                return send_file(
                    selected_path,
                    as_attachment=True,
                    download_name=os.path.basename(selected_path)
                )
            elif os.path.isdir(selected_path):
                # Create a zip of the selected directory
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for root, dirs, files in os.walk(selected_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            # Preserve the directory structure in the zip
                            relative_file_path = os.path.relpath(file_path, BASE_BACKUP_DIR)
                            zip_file.write(file_path, relative_file_path)
                            logger.debug(f"Added to ZIP: {relative_file_path}")
                zip_buffer.seek(0)
                zip_filename = f"notionbackup-{USERNAME}-{os.path.basename(selected_path)}.zip"
                logger.info(f"Serving ZIP file: {zip_filename}")
                return Response(
                    zip_buffer,
                    mimetype='application/zip',
                    headers={
                        'Content-Disposition': f'attachment; filename={zip_filename}'
                    }
                )
            else:
                logger.error(f"Selected path is neither a file nor a directory: {selected_path}")
                return jsonify({'error': "Selected path is neither a file nor a directory."}), 400

        # For multiple selections, create a zip containing all selected files and directories
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for selected_path in absolute_paths:
                if os.path.isfile(selected_path):
                    # Add the file to the zip
                    relative_path = os.path.relpath(selected_path, BASE_BACKUP_DIR)
                    zip_file.write(selected_path, relative_path)
                    logger.debug(f"Added to ZIP: {relative_path}")
                elif os.path.isdir(selected_path):
                    # Add the directory and its contents to the zip
                    for root, dirs, files in os.walk(selected_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            relative_path = os.path.relpath(file_path, BASE_BACKUP_DIR)
                            zip_file.write(file_path, relative_path)
                            logger.debug(f"Added to ZIP: {relative_path}")

        # Set the pointer to the start of the BytesIO buffer
        zip_buffer.seek(0)

        # Serve the zip file as a downloadable response with a custom name
        zip_filename = f"notionbackup-{USERNAME}-selected.zip"
        logger.info(f"Serving ZIP file: {zip_filename}")
        return Response(
            zip_buffer,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename={zip_filename}'
            }
        )

    except Exception as e:
        logger.exception(f"Error creating ZIP for selected items: {e}")
        return jsonify({'error': f"An error occurred while creating ZIP: {str(e)}"}), 500

# Route to serve static files
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    # Ensure the base backup directory exists
    if not os.path.exists(BASE_BACKUP_DIR):
        os.makedirs(BASE_BACKUP_DIR)
    app.run(host='0.0.0.0', port=5000)
