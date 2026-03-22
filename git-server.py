#!/usr/bin/env python3
import os
import subprocess
import shutil
import tarfile
import re
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configuration 
GIT_BACKEND = "/usr/lib/git-core/git-http-backend"
RCLONE_BASE_URL = "dropbox:GitRepos"
PROJECT_ROOT = os.path.abspath("./repositories")
SOURCE_HOOK_FILE = os.path.abspath("./pre-receive.py")

# Ensure local storage exists
os.makedirs(PROJECT_ROOT, exist_ok=True)

class GitCGIHandler(BaseHTTPRequestHandler):
    def get_repo_name(self):
        # 1. Extract the first part of the path
        path_no_query = self.path.split('?')[0]
        parts = [p for p in path_no_query.strip('/').split('/') if p]
    
        if not parts:
            return None
    
        # Git usually hits /repo.git/... or /repo.git/info/refs
        part = parts[0]
    
        # 2. Basic cleanup: remove .git suffix if present
        name = part
        if name.endswith('.git'):
            name = name[:-4]

        # 3. SECURITY: Path Traversal & Injection Protection
        # Use basename to prevent '../' and regex to allow only safe chars
        name = os.path.basename(name)

        # Allow only letters, numbers, underscores, and hyphens
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            print(f"--- SECURITY WARNING: Blocked suspicious repo name: {name} ---")
            return None

        # Ensure it's not a reserved name or empty
        if name.lower() in ["info", "git-upload-pack", "git-receive-pack", ""]:
            return None

        return name
    
    def sync_from_cloud(self, repo_name):
        local_repo_path = os.path.join(PROJECT_ROOT, repo_name)
        remote_bundle_path = f"{RCLONE_BASE_URL}/{repo_name}/repo.bundle.tar.gz"
        
        # 1. Check if remote bundle exists
        check = subprocess.run(["rclone", "lsf", remote_bundle_path], capture_output=True)
        
        if check.returncode == 0 and check.stdout:
            print(f"--- Rebuilding {repo_name} from Cloud Bundle ---")
            
            # Wipe existing local repo to start fresh
            if os.path.exists(local_repo_path):
                shutil.rmtree(local_repo_path)
            
            # Download compressed bundle
            subprocess.run(["rclone", "copy", remote_bundle_path, PROJECT_ROOT])
            
            # Uncompress
            temp_archive = f"{PROJECT_ROOT}/repo.bundle.tar.gz"
            with tarfile.open(temp_archive, "r:gz") as tar:
                tar.extractall(path=PROJECT_ROOT)
                # Note: This assumes the tar contains 'repo.bundle'
            
            # Create bare repo from bundle
            bundle_file = os.path.join(PROJECT_ROOT, "repo.bundle")
            subprocess.run(["git", "clone", "--bare", bundle_file, local_repo_path])
            
            # Enable pushing to this repo
            subprocess.run(["git", "-C", local_repo_path, "config", "http.receivepack", "true"])
            
            # Cleanup temp files
            os.remove(temp_archive)
            os.remove(bundle_file)
        else:
            # 2. If it doesn't exist anywhere, create a fresh bare repo
            if not os.path.exists(local_repo_path):
                print(f"--- Creating NEW bare repo: {repo_name} ---")
                subprocess.run(["git", "init", "--bare", local_repo_path])
                subprocess.run(["git", "config", "-f", f"{local_repo_path}/config", "http.receivepack", "true"])

        self.install_hooks(local_repo_path)

    def install_hooks(self, repo_path):
        hooks_dir = os.path.join(repo_path, "hooks")
        dest_hook = os.path.join(hooks_dir, "pre-receive")
        
        print(f"--- Installing pre-receive hook to {repo_path} ---")
        shutil.copy(SOURCE_HOOK_FILE, dest_hook)
        
    def sync_to_cloud(self, repo_name):
        local_repo_path = os.path.join(PROJECT_ROOT, repo_name)
        bundle_file = os.path.join(local_repo_path, "repo.bundle")
        archive_file = f"{bundle_file}.tar.gz"

        # 1. Check if the repo has any commits
        check_empty = subprocess.run(
            ["git", "-C", local_repo_path, "rev-parse", "--all"],
            capture_output=True, text=True
        )

        if not check_empty.stdout.strip():
            print(f"--- Skipping Bundle: {repo_name} is empty ---")
            return

        print(f"--- Optimizing and Bundling {repo_name} ---")
        # 2. Garbage Collection
        subprocess.run(["git", "-C", local_repo_path, "gc", "--prune=now", "--quiet"])
        
        # 3. Create Bundle (all branches)
        subprocess.run(["git", "-C", local_repo_path, "bundle", "create", bundle_file, "--all"])

        # 4. Compress
        with tarfile.open(archive_file, "w:gz") as tar:
            tar.add(bundle_file, arcname="repo.bundle")
        
        # 5. Upload to Cloud
        remote_dir = f"{RCLONE_BASE_URL}/{repo_name}/"
        subprocess.run(["rclone", "copy", archive_file, remote_dir])
        
        # Cleanup temp bundle files
        os.remove(bundle_file)
        os.remove(archive_file)
        print(f"--- Upload Complete ---")

    def handle_lfs_batch(self, repo):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        request_data = json.loads(body)

        operation = request_data.get("operation") # 'upload' or 'download'
        objects = request_data.get("objects", [])
        response_objects = []

        for obj in objects:
            oid = obj.get("oid")
            if not self.is_valid_oid(oid):
                print(f"--- SECURITY ALERT: Invalid OID in Batch: {oid} ---")
                continue
            size = obj.get("size")
            remote_path = f"{RCLONE_BASE_URL}/{repo}/lfs/{oid}"
            
            # Check if file exists on Dropbox
            # 'lsf' is fast for single file checks
            check = subprocess.run(["rclone", "lsf", remote_path], capture_output=True, text=True)
            exists = (check.returncode == 0 and check.stdout.strip() != "")

            obj_entry = {"oid": oid, "size": size}
            
            # Logic: If uploading and it exists, don't provide an 'action'
            # If downloading and it DOESN'T exist, return an error for that object
            if operation == "upload" and not exists:
                obj_entry["actions"] = {
                    "upload": {
                        "href": f"http://{self.headers['Host']}/{repo}/lfs/data/{oid}"
                    }
                }
            elif operation == "download":
                if exists:
                    obj_entry["actions"] = {
                        "download": {
                            "href": f"http://{self.headers['Host']}/{repo}/lfs/data/{oid}"
                        }
                    }
                else:
                    obj_entry["error"] = {"code": 404, "message": "Object does not exist"}

            response_objects.append(obj_entry)

        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.git-lfs+json")
        self.end_headers()
        self.wfile.write(json.dumps({"transfer": "basic", "objects": response_objects}).encode())
    
    def do_GET(self):
        repo = self.get_repo_name()
        if not repo:
            print(f"--- Rejected GET request to invalid path: {self.path} ---")
            self.send_error(403)
            return

        if "/lfs/data/" in self.path:
            self.handle_lfs_download(repo)
            return

        query = self.path.split('?')[1] if '?' in self.path else ''
        if ("git-upload-pack" in query or "git-receive-pack" in query):
            self.sync_from_cloud(repo)

        self.run_git_cgi()

    def do_POST(self):
        repo = self.get_repo_name()
        if not repo:
            print(f"--- Rejected POST request to invalid path: {self.path} ---")
            self.send_error(403)
            return

        if "/info/lfs/objects/batch" in self.path:
            self.handle_lfs_batch(repo)
            return

        self.run_git_cgi()
        if "git-receive-pack" in self.path:
            self.sync_to_cloud(repo)

    def do_PUT(self):
        repo = self.get_repo_name()
        if not repo:
            print(f"--- Rejected PUT request to invalid path: {self.path} ---")
            self.send_error(403)
            return

        # Path format: /repo.git/lfs/data/<OID>
        parts = self.path.strip('/').split('/')
        if len(parts) < 4 or parts[1] != "lfs" or parts[2] != "data":
            self.send_error(400, "Invalid LFS path")
            return

        oid = parts[3]
        if not self.is_valid_oid(oid):
            print(f"--- SECURITY ALERT: Hijack attempt via PUT OID: {oid} ---")
            self.send_error(403, "Invalid Object Identifier")
            return

        remote_path = f"{RCLONE_BASE_URL}/{repo}/lfs/{oid}"
        content_length = int(self.headers.get('Content-Length', 0))

        check_exists = subprocess.run(["rclone", "lsf", remote_path], capture_output=True, text=True)
        if check_exists.returncode == 0 and check_exists.stdout.strip():
            print(f"--- LFS: {oid} already exists on cloud. Skipping upload. ---")
            # 200 OK is appropriate here; the client thinks it succeeded
            self.send_response(200)
            self.end_headers()
            return

        print(f"--- LFS Upload: {oid} ({content_length} bytes) ---")

        # Use rclone rcat to stream stdin directly to the cloud
        # This prevents the container from running out of disk space
        process = subprocess.Popen(
            ["rclone", "rcat", remote_path],
            stdin=subprocess.PIPE
        )

        # Read from the HTTP request and write to rclone in chunks
        remaining = content_length
        chunk_size = 64 * 1024  # 64KB chunks
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, chunk_size))
            if not chunk:
                break
            process.stdin.write(chunk)
            remaining -= len(chunk)

        process.stdin.close()
        process.wait()

        if process.returncode == 0:
            self.send_response(200)
            self.end_headers()
        else:
            self.send_error(500, "Rclone upload failed")

    def run_git_cgi(self):
        env = {
            'REQUEST_METHOD': self.command,
            'GIT_PROJECT_ROOT': PROJECT_ROOT,
            'GIT_HTTP_EXPORT_ALL': '1',
            'PATH_INFO': self.path.split('?')[0],
            'QUERY_STRING': self.path.split('?')[1] if '?' in self.path else '',
            'CONTENT_TYPE': self.headers.get('Content-Type', '')
        }
        content_length = int(self.headers.get('Content-Length', 0))
        input_data = self.rfile.read(content_length) if content_length > 0 else None
        
        process = subprocess.Popen([GIT_BACKEND], env=env, stdin=subprocess.PIPE, 
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _ = process.communicate(input=input_data)
        
        header_end = stdout.find(b"\r\n\r\n")
        if header_end == -1:
            self.send_error(500, "CGI script error")
            return
        self.send_response(200)
        raw_headers = stdout[:header_end].decode().split("\r\n")
        for h in raw_headers:
            if ":" in h:
                k, v = h.split(":", 1)
                self.send_header(k.strip(), v.strip())
        self.end_headers()
        self.wfile.write(stdout[header_end+4:])

    def handle_lfs_download(self, repo):
        oid = self.path.strip('/').split('/')[-1]
        if not self.is_valid_oid(oid):
            print(f"--- SECURITY ALERT: Hijack attempt via GET OID: {oid} ---")
            self.send_error(403, "Invalid Object Identifier")
            return
        remote_path = f"{RCLONE_BASE_URL}/{repo}/lfs/{oid}"

        print(f"--- LFS Download: {oid} ---")

        # Use rclone cat to stream from cloud to the HTTP response
        process = subprocess.Popen(
            ["rclone", "cat", remote_path],
            stdout=subprocess.PIPE
        )

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

        # Pipe rclone output to the network socket
        shutil.copyfileobj(process.stdout, self.wfile)
        process.wait()

    def is_valid_oid(self, oid):
        # SHA-256 is 64 hex characters: ^[0-9a-f]{64}$
        if not oid or not isinstance(oid, str):
            return False
        return bool(re.match(r'^[0-9a-f]{64}$', oid.lower()))

if __name__ == "__main__":
    try:
        print("Serving Git at http://localhost:8080")
        HTTPServer(('localhost', 8080), GitCGIHandler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
