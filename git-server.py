import os
import subprocess
import stat
import sys
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler

# CONFIGURATION
GIT_BACKEND = "/usr/lib/git-core/git-http-backend" 
PROJECT_ROOT = os.path.abspath("./repositories")
# The source file next to this script
SOURCE_HOOK_FILE = os.path.abspath("pre-receive.py")
POST_RECEIVE_FILE = os.path.abspath("post-receive.py")

class GitCGIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.run_git_cgi()

    def do_POST(self):
        self.run_git_cgi()

    def run_git_cgi(self):
        env = {
            'REQUEST_METHOD': self.command,
            'GIT_PROJECT_ROOT': PROJECT_ROOT,
            'GIT_HTTP_EXPORT_ALL': '1',
            'PATH_INFO': self.path.split('?')[0],
            'QUERY_STRING': self.path.split('?')[1] if '?' in self.path else '',
            'CONTENT_TYPE': self.headers.get('Content-Type', ''),
        }

        content_length = int(self.headers.get('Content-Length', 0))
        input_data = self.rfile.read(content_length) if content_length > 0 else None

        process = subprocess.Popen(
            [GIT_BACKEND],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

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

def setup_repo(name):
    """Initializes a bare repo and copies the hook file."""
    repo_path = os.path.join(PROJECT_ROOT, f"{name}.git")
    if not os.path.exists(repo_path):
        print(f"--- Initializing {name} ---")
        subprocess.run(["git", "init", "--bare", repo_path])
        subprocess.run(["git", "config", "-f", f"{repo_path}/config", "http.receivepack", "true"])
        
        shutil.copy(SOURCE_HOOK_FILE, os.path.join(repo_path, "hooks", "pre-receive"))
        shutil.copy(POST_RECEIVE_FILE, os.path.join(repo_path, "hooks", "post-receive"))

if __name__ == "__main__":
    if not os.path.exists(PROJECT_ROOT):
        os.makedirs(PROJECT_ROOT)
    
    # Initialize the repo
    setup_repo("test")

    print(f"Serving Git via CGI at http://localhost:8080")
    HTTPServer(('localhost', 8080), GitCGIHandler).serve_forever()
