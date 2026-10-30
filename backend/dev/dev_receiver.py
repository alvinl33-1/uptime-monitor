import os
from http.server import BaseHTTPRequestHandler, HTTPServer

FAIL_FILE = "/tmp/fail"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        failing = os.path.exists(FAIL_FILE)
        key = self.headers.get("Idempotency-Key")
        print(f"{'FAIL' if failing else 'OK'} key={key} body={body}", flush=True)
        self.send_response(500 if failing else 200)
        self.end_headers()

    def log_message(self, *args):
        pass



if __name__ == "__main__":
    print("receiver listening on :9000", flush=True)
    HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()