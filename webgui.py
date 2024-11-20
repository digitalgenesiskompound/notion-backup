from flask import Flask, send_from_directory
import os

app = Flask(__name__)

BACKUP_DIR = "/notion-backup"

@app.route('/')
def index():
    files = os.listdir(BACKUP_DIR)
    file_links = [f"<a href='/download/{file}'>{file}</a>" for file in files]
    return "<br>".join(file_links)

@app.route('/download/<path:filename>')
def download_file(filename):
    return send_from_directory(BACKUP_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
