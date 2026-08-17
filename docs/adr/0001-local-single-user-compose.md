# Run as a local single-user Docker Compose application

Kindrop runs on demand on one personal computer, binds only to `127.0.0.1`, and has no application login. Docker Compose isolates KCC and its archive dependencies from the host while keeping the product free to operate; supporting LAN access or multiple users would require a separate authentication and isolation design.

