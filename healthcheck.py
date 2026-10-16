"""Read-only health check over the private Unix socket (no network required)."""
import http.client
import json
import socket


class UnixHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/run/waze/app.sock')


if __name__ == '__main__':
    client = UnixHTTPConnection('localhost', timeout=5)
    try:
        client.request('GET', '/waze/healthz')
        response = client.getresponse()
        if response.status != 200 or json.loads(response.read()) != {'status': 'ok'}:
            raise SystemExit('Unhealthy Waze service')
    finally:
        client.close()
