import argparse
from http.server import ThreadingHTTPServer

from program.database import initialize_database
from program.http_api import make_handler


def ready_handler(database):
    class Handler(make_handler(database)):
        def do_GET(self):
            if self.path == "/healthz":
                self._send_json(200, {"status": "ready"})
                return
            super().do_GET()

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the radio HTTP service")
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", choices=("127.0.0.1",), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    initialize_database(args.db)
    with ThreadingHTTPServer((args.host, args.port), ready_handler(args.db)) as server:
        server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
