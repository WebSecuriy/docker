These are public, disposable lab certificates and a public test private key. They are
only for `receiver`, `localhost`, `127.0.0.1` and `::1`. Never use this key outside
the lab. Production/VM certificates must be generated separately with the actual SAN.

Regenerate from the repository root with OpenSSL:

```sh
openssl req -x509 -newkey rsa:2048 -nodes -keyout tests/certs/server-key.pem -out tests/certs/server.pem -days 3650 -subj '/CN=receiver' -addext 'subjectAltName=DNS:receiver,DNS:localhost,IP:127.0.0.1,IP:::1' -addext 'basicConstraints=critical,CA:TRUE'
cp tests/certs/server.pem tests/certs/ca.pem
```
