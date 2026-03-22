#!/usr/bin/env python3
import os
import subprocess
import stat
import sys
import shutil
import tarfile
from http.server import HTTPServer, BaseHTTPRequestHandler

# CONFIGURATION
GIT_BACKEND = "/usr/lib/git-core/git-http-backend"
RCLONE_BASE_URL = "dropbox:GitRepos"
PROJECT_ROOT = os.path.abspath("./repositories")
SOURCE_HOOK_FILE = os.path.abspath("./pre-receive.py")

# Ensure local storage exists
os.makedirs(PROJECT_ROOT, exist_ok=True)

class GitCGIHandler(BaseHTTPRequestHandler):
    def get_repo_name(self):
        parts = self.path.strip('/').split('/')
        part = parts[0]
        if part == "info":
            return None
        if ".git" in part:
            return p.split('?')[0]
        return part 

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

        print(f"--- Optimizing and Bundling {repo_name} ---")
        # 1. Garbage Collection
        subprocess.run(["git", "-C", local_repo_path, "gc", "--prune=now", "--quiet"])
        
        # 2. Create Bundle (all branches)
        subprocess.run(["git", "-C", local_repo_path, "bundle", "create", bundle_file, "--all"])
        
        # 3. Compress
        with tarfile.open(archive_file, "w:gz") as tar:
            tar.add(bundle_file, arcname="repo.bundle")
        
        # 4. Upload to Cloud
        remote_dir = f"{RCLONE_BASE_URL}/{repo_name}/"
        subprocess.run(["rclone", "copy", archive_file, remote_dir])
        
        # Cleanup temp bundle files
        os.remove(bundle_file)
        os.remove(archive_file)
        print(f"--- Upload Complete ---")

    def do_GET(self):
        repo = self.get_repo_name()
        query = self.path.split('?')[1] if '?' in self.path else ''
        if repo and ("git-upload-pack" in query or "git-receive-pack" in query):
            self.sync_from_cloud(repo)

        self.run_git_cgi()

    def do_POST(self):
        repo = self.get_repo_name()

        self.run_git_cgi()
        if repo and "git-receive-pack" in self.path:
            self.sync_to_cloud(repo)

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

if __name__ == "__main__":
    try:
        print("Serving Git at http://localhost:8080")
        HTTPServer(('localhost', 8080), GitCGIHandler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
