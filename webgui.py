from flask import Flask, send_file, send_from_directory, abort, render_template, request, Response, jsonify
import os
import zipfile
import io

app = Flask(__name__)

BACKUP_DIR = "/notion-backup"
BASE_BACKUP_DIR = os.path.join(BACKUP_DIR, 'backup')  # Define base backup directory

USERNAME = "username"  # Replace with actual logic to obtain username if needed.

def secure_path(path):
    # Ensure the path is secure and within BASE_BACKUP_DIR
    abs_path = os.path.abspath(os.path.join(BASE_BACKUP_DIR, path))
    if not abs_path.startswith(os.path.abspath(BASE_BACKUP_DIR)):
        abort(403)  # Forbidden
    return abs_path

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/list', methods=['GET'])
def list_directory():
    path = request.args.get('path', '')
    try:
        current_dir = secure_path(path)
        if not os.path.exists(current_dir):
            return jsonify({'error': 'Directory not found'}), 404

        directories = []
        files = []
        for item in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item)
            if os.path.isdir(item_path):
                directories.append(item)
            elif os.path.isfile(item_path):
                files.append(item)

        directories.sort()
        files.sort()

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
        return jsonify({'error': str(e)}), 500

@app.route('/download_all')
def download_all():
    try:
        if not os.path.exists(BASE_BACKUP_DIR):
            return jsonify({'error': 'Backup directory not found.'}), 500

        # Create a zip file in-memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, _, files in os.walk(BASE_BACKUP_DIR):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Add the file to the zip, preserving its relative path
                    relative_path = os.path.relpath(file_path, BASE_BACKUP_DIR)
                    zip_file.write(file_path, relative_path)

        # Set the pointer to the start of the BytesIO buffer
        zip_buffer.seek(0)

        # Serve the zip file as a downloadable response with a custom name
        zip_filename = f"notionbackup-{USERNAME}-all.zip"
        return Response(
            zip_buffer,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename={zip_filename}'
            }
        )

    except Exception as e:
        return jsonify({'error': f"An error occurred while creating ZIP: {str(e)}"}), 500

@app.route('/download_selected', methods=['POST'])
def download_selected():
    try:
        # Get the list of selected paths from the JSON payload
        selected_paths = request.json.get('selected_paths', [])

        if not selected_paths:
            return jsonify({'error': 'No files or directories selected for download.'}), 400

        # Secure and validate all selected paths
        absolute_paths = [secure_path(path) for path in selected_paths]

        # If only one file or directory is selected, handle accordingly
        if len(absolute_paths) == 1:
            selected_path = absolute_paths[0]
            relative_path = os.path.relpath(selected_path, BASE_BACKUP_DIR)

            if os.path.isfile(selected_path):
                # Serve the single file
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
                zip_buffer.seek(0)
                zip_filename = f"notionbackup-{USERNAME}-{os.path.basename(selected_path)}.zip"
                return Response(
                    zip_buffer,
                    mimetype='application/zip',
                    headers={
                        'Content-Disposition': f'attachment; filename={zip_filename}'
                    }
                )
            else:
                return jsonify({'error': "Selected path is neither a file nor a directory."}), 400

        # For multiple selections, create a zip containing all selected files and directories
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for selected_path in absolute_paths:
                if os.path.isfile(selected_path):
                    # Add the file to the zip
                    relative_path = os.path.relpath(selected_path, BASE_BACKUP_DIR)
                    zip_file.write(selected_path, relative_path)
                elif os.path.isdir(selected_path):
                    # Add the directory and its contents to the zip
                    for root, dirs, files in os.walk(selected_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            relative_path = os.path.relpath(file_path, BASE_BACKUP_DIR)
                            zip_file.write(file_path, relative_path)

        # Set the pointer to the start of the BytesIO buffer
        zip_buffer.seek(0)

        # Serve the zip file as a downloadable response with a custom name
        zip_filename = f"notionbackup-{USERNAME}-selected.zip"
        return Response(
            zip_buffer,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename={zip_filename}'
            }
        )

    except Exception as e:
        return jsonify({'error': f"An error occurred while creating ZIP: {str(e)}"}), 500

# Route to serve static files
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
