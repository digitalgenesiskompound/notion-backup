// scripts.js

let currentPath = '';
let allDirectories = [];
let allFiles = [];
let currentSort = 'name_asc';
let currentSearch = '';
let isGlobalSearch = false; // Toggle between current directory and global search
let selectedItems = new Set(); // To store selected item paths
let navigationHistory = []; // To manage back navigation

document.addEventListener('DOMContentLoaded', () => {
    loadDirectory('');
    loadSidebar();
    setupSelectedItemsPanel();
    setupNavigationButtons();
    setupEditor();
});

// Function to setup the Selected Items Side Panel
function setupSelectedItemsPanel() {
    const selectedItemsList = document.getElementById('selected-items-list');
    const fileListForm = document.getElementById('file-list-form');

    // Toggle the side panel visibility
    fileListForm.addEventListener('change', (e) => {
        if (e.target.classList.contains('file-checkbox')) {
            const path = e.target.value;
            if (e.target.checked) {
                selectedItems.add(path);
            } else {
                selectedItems.delete(path);
            }
            updateSelectedItemsPanel();
        }
    });

    // Allow clicking on selected items to deselect them
    selectedItemsList.addEventListener('click', (e) => {
        if (e.target.tagName === 'LI') {
            const path = e.target.getAttribute('data-path');
            selectedItems.delete(path);
            // Uncheck the corresponding checkbox
            const checkbox = document.querySelector(`.file-checkbox[value="${path}"]`);
            if (checkbox) {
                checkbox.checked = false;
            }
            updateSelectedItemsPanel();
        }
    });
}

// Function to update the Selected Items Side Panel
function updateSelectedItemsPanel() {
    const selectedItemsList = document.getElementById('selected-items-list');
    selectedItemsList.innerHTML = '';

    selectedItems.forEach(path => {
        const listItem = document.createElement('li');
        listItem.textContent = path;
        listItem.setAttribute('data-path', path);
        selectedItemsList.appendChild(listItem);
    });

    // Show or hide the side panel based on selections
    const sidePanel = document.getElementById('selected-items-panel');
    if (selectedItems.size > 0) {
        sidePanel.style.display = 'block';
    } else {
        sidePanel.style.display = 'none';
    }
}

// Function to close the Selected Items Side Panel
function closeSelectedItemsPanel() {
    selectedItems.clear();
    updateSelectedItemsPanel();
    // Uncheck all checkboxes
    const checkboxes = document.querySelectorAll('.file-checkbox');
    checkboxes.forEach(checkbox => {
        checkbox.checked = false;
    });
}

// Function to perform actions (Download, Edit, Delete) on selected items
function performAction(action) {
    if (selectedItems.size === 0) {
        alert('No items selected.');
        return;
    }

    const paths = Array.from(selectedItems);
    if (action === 'download') {
        downloadItems(paths);
    } else if (action === 'delete') {
        deleteItems(paths);
    } else if (action === 'edit') {
        editSelectedItem(paths);
    }
}

// Function to edit selected item
function editSelectedItem(paths) {
    if (paths.length !== 1) {
        alert('Please select exactly one file to edit.');
        return;
    }

    const path = paths[0];
    // Check if the selected path is a file
    const selectedFile = allFiles.find(file => file.path === path);
    if (!selectedFile) {
        alert('Selected item is not a file or does not exist.');
        return;
    }

    openEditor(path);
}

// Consolidated Function to Download Items
function downloadItems(paths) {
    if (paths.length === 1 && !allDirectories.includes(paths[0])) {
        // Single file download
        downloadSingleFile(paths[0]);
    } else {
        // Multiple files or directories download as ZIP
        downloadMultipleItems(paths);
    }
}

// Function to download a single file
function downloadSingleFile(path) {
    showLoading(true);
    fetch('/download_selected', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ selected_paths: [path] }),
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('Failed to download the file');
        }
        return response.blob();
    })
    .then(blob => {
        const filename = path.split('/').pop();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    })
    .catch(error => {
        console.error('Error downloading the file:', error);
        alert('Failed to download the file.');
    })
    .finally(() => {
        showLoading(false);
    });
}

// Function to download multiple items as a ZIP with progress
function downloadMultipleItems(paths) {
    showLoading(true);
    showProgress(true);
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    progressBar.style.width = '0%';
    progressText.textContent = '0%';
    progressBar.classList.remove('indeterminate');

    fetch('/download_selected', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ selected_paths: paths }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => { throw data; });
        }

        const contentLength = response.headers.get('Content-Length');
        if (contentLength) {
            const total = parseInt(contentLength, 10);
            let loaded = 0;

            return new Response(
                new ReadableStream({
                    start(controller) {
                        const reader = response.body.getReader();
                        function read() {
                            reader.read().then(({ done, value }) => {
                                if (done) {
                                    controller.close();
                                    return;
                                }
                                loaded += value.byteLength;
                                const percent = ((loaded / total) * 100).toFixed(2);
                                progressBar.style.width = `${percent}%`;
                                progressText.textContent = `${percent}%`;
                                controller.enqueue(value);
                                read();
                            }).catch(error => {
                                console.error('Error reading stream:', error);
                                controller.error(error);
                            });
                        }
                        read();
                    }
                })
            );
        } else {
            // If Content-Length is not provided, show an indeterminate progress bar
            progressBar.classList.add('indeterminate');
            progressText.textContent = 'Downloading...';
            return response.blob();
        }
    })
    .then(response => {
        if (response instanceof Blob) {
            const disposition = response.headers ? response.headers.get('Content-Disposition') : null;
            let filename = 'download.zip';
            if (disposition && disposition.indexOf('filename=') !== -1) {
                const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                const matches = filenameRegex.exec(disposition);
                if (matches != null && matches[1]) { 
                    filename = matches[1].replace(/['"]/g, '');
                }
            }
            const url = window.URL.createObjectURL(response);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            alert('Download completed successfully.');
        }
    })
    .catch(error => {
        console.error('Error downloading selected items:', error);
        alert(error.error || 'An error occurred while downloading the selected items.');
    })
    .finally(() => {
        showLoading(false);
        showProgress(false);
        // Remove indeterminate class if present
        const progressBar = document.getElementById('progress-bar');
        progressBar.classList.remove('indeterminate');
    });
}

// Function to delete items
function deleteItems(paths) {
    if (!confirm(`Are you sure you want to delete ${paths.length} item(s)? This action cannot be undone.`)) {
        return;
    }

    showLoading(true);
    fetch('/delete', {  // Ensure this endpoint matches the backend route
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ path: paths }), // Send array
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => { throw data; });
        }
        return response.json();
    })
    .then(data => {
        if (data.error) {
            alert(data.error);
        } else {
            if (data.errors && data.errors.length > 0) {
                let message = 'Some items were not deleted:\n';
                data.errors.forEach(err => {
                    message += `- ${err.path}: ${err.error}\n`;
                });
                alert(message);
            } else {
                alert('Items deleted successfully.');
            }
            selectedItems.clear();
            updateSelectedItemsPanel();
            loadDirectory(currentPath); // Refresh the directory view
        }
        showLoading(false);
    })
    .catch(error => {
        console.error('Error deleting items:', error);
        alert(error.error || 'An error occurred while deleting the items.');
        showLoading(false);
    });
}

// Function to load directory contents
function loadDirectory(path) {
    currentPath = path;
    isGlobalSearch = false; // Reset to current directory search
    document.getElementById('search-bar').value = ''; // Clear search bar
    showLoading(true);
    fetch(`/api/list?path=${encodeURIComponent(path)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert(data.error);
                showLoading(false);
                return;
            }
            allDirectories = data.directories.map(dir => {
                // Construct relative path
                return path ? `${path}/${dir}`.replace(/\\/g, '/') : dir;
            });
            allFiles = data.files;
            updateBreadcrumb(data.breadcrumb);
            applyFilters();
            showLoading(false);
        })
        .catch(error => {
            console.error('Error fetching directory:', error);
            alert('An error occurred while loading the directory.');
            showLoading(false);
        });
}

// Function to load sidebar navigation
function loadSidebar() {
    fetch('/api/list?path=')
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                console.error('Error loading sidebar:', data.error);
                return;
            }
            const sidebarList = document.getElementById('sidebar-list');
            sidebarList.innerHTML = '';
            data.directories.forEach(directory => {
                const fullPath = directory; // Relative path
                const listItem = document.createElement('li');
                const link = document.createElement('a');
                link.href = '#';
                link.textContent = directory.split('/').pop(); // Display only the directory name
                link.setAttribute('data-path', fullPath);
                link.addEventListener('click', (e) => {
                    e.preventDefault();
                    const newPath = fullPath;
                    navigationHistory.push(currentPath); // Push current path to history before navigating
                    loadDirectory(newPath);
                });
                listItem.appendChild(link);
                sidebarList.appendChild(listItem);
            });
        })
        .catch(error => {
            console.error('Error loading sidebar:', error);
        });
}

// Function to update breadcrumb navigation
function updateBreadcrumb(breadcrumb) {
    const breadcrumbLinks = document.getElementById('breadcrumb-links');
    breadcrumbLinks.innerHTML = ''; // Clear existing breadcrumb links

    breadcrumb.forEach((crumb, index) => {
        const link = document.createElement('a');
        link.href = '#';
        link.textContent = crumb.name;
        link.setAttribute('data-path', crumb.path);
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const newPath = crumb.path;
            navigationHistory.push(currentPath); // Push current path to history before navigating
            loadDirectory(newPath);
        });
        breadcrumbLinks.appendChild(link);
        if (index < breadcrumb.length - 1) {
            const separator = document.createTextNode(' / ');
            breadcrumbLinks.appendChild(separator);
        }
    });
}

// Function to apply filters based on search and sort
function applyFilters() {
    if (isGlobalSearch && currentSearch.trim() !== '') {
        // Use the global search results directly without performing another search
        updateFileList(allDirectories, allFiles);
        return;
    }

    let filteredDirectories = allDirectories.filter(dir => dir.toLowerCase().includes(currentSearch.toLowerCase()));
    let filteredFiles = allFiles.filter(file => file.name.toLowerCase().includes(currentSearch.toLowerCase()));

    // Sort directories and files based on currentSort
    filteredDirectories = sortArray(filteredDirectories, currentSort, 'directory');
    filteredFiles = sortArray(filteredFiles, currentSort, 'file');

    updateFileList(filteredDirectories, filteredFiles);
}

// Sorting function
function sortArray(arr, sortType, type='directory') {
    let sortedArr = [...arr]; // Clone the array to avoid in-place sorting
    if (type === 'directory') {
        // Directories can be sorted by name or date
        if (sortType.startsWith('name')) {
            sortedArr.sort((a, b) => {
                if (a.toLowerCase() < b.toLowerCase()) return sortType === 'name_asc' ? -1 : 1;
                if (a.toLowerCase() > b.toLowerCase()) return sortType === 'name_asc' ? 1 : -1;
                return 0;
            });
        } else if (sortType.startsWith('date')) {
            // Assuming directories have a 'lastModified' attribute (if not, you might need to modify the backend)
            // For simplicity, we'll sort directories by name if date is not available
            sortedArr.sort((a, b) => {
                // Placeholder: directories don't have 'lastModified', so sort by name
                if (a.toLowerCase() < b.toLowerCase()) return sortType === 'date_asc' ? -1 : 1;
                if (a.toLowerCase() > b.toLowerCase()) return sortType === 'date_asc' ? 1 : -1;
                return 0;
            });
        }
        // Add more sorting types for directories if needed
    } else if (type === 'file') {
        if (sortType.startsWith('name')) {
            sortedArr.sort((a, b) => {
                if (a.name.toLowerCase() < b.name.toLowerCase()) return sortType === 'name_asc' ? -1 : 1;
                if (a.name.toLowerCase() > b.name.toLowerCase()) return sortType === 'name_asc' ? 1 : -1;
                return 0;
            });
        } else if (sortType.startsWith('date')) {
            sortedArr.sort((a, b) => {
                let aDate = new Date(a.lastModified * 1000); // Assuming lastModified is a Unix timestamp in seconds
                let bDate = new Date(b.lastModified * 1000);
                if (aDate < bDate) return sortType === 'date_asc' ? -1 : 1;
                if (aDate > bDate) return sortType === 'date_asc' ? 1 : -1;
                return 0;
            });
        } else if (sortType.startsWith('size')) {
            sortedArr.sort((a, b) => {
                if (a.size < b.size) return sortType === 'size_asc' ? -1 : 1;
                if (a.size > b.size) return sortType === 'size_asc' ? 1 : -1;
                return 0;
            });
        }
    }
    return sortedArr;
}

// Function to update the file and directory list
function updateFileList(directories, files) {
    const fileList = document.getElementById('file-list');
    fileList.innerHTML = ''; // Clear existing list

    // Add directories
    directories.forEach(directory => {
        const listItem = document.createElement('li');
        listItem.className = 'file-list-item';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'selected_paths';
        checkbox.value = directory; // Use the full relative path
        checkbox.className = 'file-checkbox';
        if (selectedItems.has(directory)) {
            checkbox.checked = true;
        }

        const link = document.createElement('a');
        link.href = '#';
        link.className = 'file-link';
        link.innerHTML = `<i class="bi bi-folder-fill file-icon"></i> ${getDirectoryName(directory)}`; // Display only the directory name
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const newPath = directory; // Use the full path directly
            navigationHistory.push(currentPath); // Push current path to history before navigating
            loadDirectory(newPath);
        });

        // File details (e.g., Folder)
        const details = document.createElement('div');
        details.className = 'file-details';
        details.textContent = 'Folder';

        listItem.appendChild(checkbox);
        listItem.appendChild(link);
        listItem.appendChild(details);
        fileList.appendChild(listItem);
    });

    // Add files
    files.forEach(file => {
        const listItem = document.createElement('li');
        listItem.className = 'file-list-item';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'selected_paths';
        checkbox.value = file.path; // Use the full path provided by the backend
        checkbox.className = 'file-checkbox';
        if (selectedItems.has(file.path)) {
            checkbox.checked = true;
        }

        const span = document.createElement('span');
        span.className = 'file-link';
        span.innerHTML = `<i class="bi bi-file-earmark-fill file-icon"></i> ${file.name}`;
        span.addEventListener('click', () => {
            // Optionally, implement file preview or download
        });

        // File details (e.g., size and last modified)
        const details = document.createElement('div');
        details.className = 'file-details';
        details.textContent = `${formatSize(file.size)} | ${formatDate(file.lastModified)}`;

        listItem.appendChild(checkbox);
        listItem.appendChild(span);
        listItem.appendChild(details);
        fileList.appendChild(listItem);
    });
}

// Function to setup Monaco Editor
function setupEditor() {
    require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.34.1/min/vs' }});
    require(['vs/editor/editor.main'], function() {
        editor = monaco.editor.create(document.getElementById('editor'), {
            value: '',
            language: 'plaintext',
            theme: 'vs-dark',
            automaticLayout: true
        });
    });
}

// Function to open the editor modal and load file content
function openEditor(filePath) {
    currentEditingFilePath = filePath;
    fetch(`/api/get_file_content?path=${encodeURIComponent(filePath)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert(data.error);
                return;
            }
            editor.setValue(data.content);
            showEditorModal(true);
            // Optionally, set language based on file extension
            const extension = getFileExtension(filePath);
            setEditorLanguage(extension);
        })
        .catch(error => {
            console.error('Error fetching file content:', error);
            alert('An error occurred while fetching the file content.');
        });
}

// Function to close the editor modal
function closeEditor() {
    showEditorModal(false);
    editor.setValue(''); // Clear editor content
    currentEditingFilePath = '';
}

// Function to show or hide the editor modal
function showEditorModal(show) {
    const modal = document.getElementById('editor-modal');
    if (show) {
        modal.style.display = 'block';
    } else {
        modal.style.display = 'none';
    }
}

// Function to save the edited file
function saveEditor() {
    const editedContent = editor.getValue();
    fetch('/api/save_file_content', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            path: currentEditingFilePath,
            content: editedContent
        }),
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert(data.error);
        } else {
            alert('File saved successfully.');
            closeEditor();
            loadDirectory(currentPath); // Refresh the directory view
        }
    })
    .catch(error => {
        console.error('Error saving file:', error);
        alert('An error occurred while saving the file.');
    });
}

// Helper Function to get file extension
function getFileExtension(path) {
    return path.split('.').pop().toLowerCase();
}

// Function to set editor language based on file extension
function setEditorLanguage(extension) {
    const languageMapping = {
        'js': 'javascript',
        'py': 'python',
        'md': 'markdown',
        'html': 'html',
        'css': 'css',
        'json': 'json',
        'java': 'java',
        'c': 'c',
        'cpp': 'cpp',
        'cs': 'csharp',
        // Add more mappings as needed
    };
    const language = languageMapping[extension] || 'plaintext';
    monaco.editor.setModelLanguage(editor.getModel(), language);
}

// Helper Function to Extract Directory Name from Path
function getDirectoryName(path) {
    const parts = path.split('/');
    return parts[parts.length - 1];
}

// Function to format file size
function formatSize(bytes) {
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    if (bytes === 0) return '0 Byte';
    const i = parseInt(Math.floor(Math.log(bytes) / Math.log(1024)));
    return Math.round(bytes / Math.pow(1024, i), 2) + ' ' + sizes[i];
}

// Function to format date
function formatDate(timestamp) {
    const date = new Date(timestamp * 1000); // Convert Unix timestamp to milliseconds
    return date.toLocaleString();
}

// Function to search files and folders (current directory and globally)
function searchFiles() {
    const query = document.getElementById('search-bar').value.trim();
    currentSearch = query;
    if (query === '') {
        isGlobalSearch = false;
        // Reload the current directory to reset the view and breadcrumbs
        loadDirectory(currentPath);
        return;
    }

    // Treat any search input as a global search
    isGlobalSearch = true;
    showLoading(true);
    performGlobalSearch(query);
}

// Function to perform global search (recursive search)
function performGlobalSearch(query) {
    fetch(`/api/search?query=${encodeURIComponent(query)}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert(data.error);
                showLoading(false);
                return;
            }
            allDirectories = data.directories;
            allFiles = data.files;
            updateBreadcrumb(data.breadcrumb); // Update breadcrumbs to show 'Search Results'
            applyFilters();
            showLoading(false);
        })
        .catch(error => {
            console.error('Error performing search:', error);
            alert('An error occurred while searching.');
            showLoading(false);
        });
}

// Function to sort files and folders
function sortFiles() {
    const sortType = document.getElementById('sort-dropdown').value;
    currentSort = sortType;
    applyFilters();
}

// Function to download all files as a ZIP with progress
function downloadAll() {
    showLoading(true);
    showProgress(true);
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    progressBar.style.width = '0%';
    progressText.textContent = '0%';
    progressBar.classList.remove('indeterminate');

    fetch('/download_all')
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => { throw data; });
            }

            const contentLength = response.headers.get('Content-Length');
            if (contentLength) {
                const total = parseInt(contentLength, 10);
                let loaded = 0;

                return new Response(
                    new ReadableStream({
                        start(controller) {
                            const reader = response.body.getReader();
                            function read() {
                                reader.read().then(({ done, value }) => {
                                    if (done) {
                                        controller.close();
                                        return;
                                    }
                                    loaded += value.byteLength;
                                    const percent = ((loaded / total) * 100).toFixed(2);
                                    progressBar.style.width = `${percent}%`;
                                    progressText.textContent = `${percent}%`;
                                    controller.enqueue(value);
                                    read();
                                }).catch(error => {
                                    console.error('Error reading stream:', error);
                                    controller.error(error);
                                });
                            }
                            read();
                        }
                    })
                );
            } else {
                // If Content-Length is not provided, show an indeterminate progress bar
                progressBar.classList.add('indeterminate');
                progressText.textContent = 'Downloading...';
                return response.blob();
            }
        })
        .then(response => {
            if (response instanceof Blob) {
                const disposition = response.headers ? response.headers.get('Content-Disposition') : null;
                let filename = 'download.zip';
                if (disposition && disposition.indexOf('filename=') !== -1) {
                    const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                    const matches = filenameRegex.exec(disposition);
                    if (matches != null && matches[1]) { 
                        filename = matches[1].replace(/['"]/g, '');
                    }
                }
                const url = window.URL.createObjectURL(response);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
                alert('Download completed successfully.');
            }
        })
        .catch(error => {
            console.error('Error downloading all items:', error);
            alert(error.error || 'An error occurred while downloading all items.');
        })
        .finally(() => {
            showLoading(false);
            showProgress(false);
            // Remove indeterminate class if present
            const progressBar = document.getElementById('progress-bar');
            progressBar.classList.remove('indeterminate');
        });
}

// Function to upload files
function uploadFiles() {
    const input = document.getElementById('upload-input');
    const files = input.files;
    if (files.length === 0) {
        return;
    }

    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append('files', files[i]);
    }
    formData.append('path', currentPath);

    showLoading(true);
    fetch('/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert(data.error);
        } else {
            alert('Files uploaded successfully.');
            loadDirectory(currentPath); // Refresh the directory view
        }
        showLoading(false);
    })
    .catch(error => {
        console.error('Error uploading files:', error);
        alert('An error occurred while uploading files.');
        showLoading(false);
    });

    // Reset the input
    input.value = '';
}

// Function to show or hide the loading spinner
function showLoading(show) {
    const loadingDiv = document.getElementById('loading');
    if (show) {
        loadingDiv.style.display = 'flex';
    } else {
        loadingDiv.style.display = 'none';
    }
}

// Function to show or hide the progress bar
function showProgress(show) {
    const progressContainer = document.getElementById('progress-container');
    if (show) {
        progressContainer.style.display = 'block';
    } else {
        progressContainer.style.display = 'none';
    }
}

// Function to setup navigation buttons (Back and Home)
function setupNavigationButtons() {
    const backButton = document.getElementById('back-button');
    const homeButton = document.getElementById('home-button');

    backButton.addEventListener('click', () => {
        if (navigationHistory.length > 0) {
            const previousPath = navigationHistory.pop();
            loadDirectory(previousPath);
        } else {
            alert('No previous directory.');
        }
    });

    homeButton.addEventListener('click', () => {
        if (currentPath !== '') {
            navigationHistory.push(currentPath); // Push current path to history before navigating
            loadDirectory('');
        }
    });
}
