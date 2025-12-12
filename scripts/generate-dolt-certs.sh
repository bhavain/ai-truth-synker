#!/bin/bash
# Generate self-signed SSL certificates for Dolt server
# These are for development use only - use proper CA-signed certs in production

set -e

CERT_DIR="./certs/dolt"

# Create directory if it doesn't exist
mkdir -p "$CERT_DIR"

echo "Generating self-signed SSL certificates for Dolt..."

# Generate CA key and certificate
openssl genrsa 2048 > "$CERT_DIR/ca-key.pem"
openssl req -new -x509 -nodes -days 3650 -key "$CERT_DIR/ca-key.pem" \
  -out "$CERT_DIR/ca-cert.pem" \
  -subj "/CN=Dolt-CA"

# Create OpenSSL config for SAN
cat > "$CERT_DIR/openssl.cnf" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = localhost

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
IP.2 = 0.0.0.0
EOF

# Generate server key and certificate signing request
openssl req -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$CERT_DIR/server-key.pem" \
  -out "$CERT_DIR/server-req.pem" \
  -config "$CERT_DIR/openssl.cnf"

# Generate server certificate with SAN
openssl x509 -req -days 3650 -set_serial 01 \
  -in "$CERT_DIR/server-req.pem" \
  -out "$CERT_DIR/server-cert.pem" \
  -CA "$CERT_DIR/ca-cert.pem" \
  -CAkey "$CERT_DIR/ca-key.pem" \
  -extensions v3_req \
  -extfile "$CERT_DIR/openssl.cnf"

# Set proper permissions
chmod 600 "$CERT_DIR/server-key.pem"
chmod 644 "$CERT_DIR/server-cert.pem"
chmod 644 "$CERT_DIR/ca-cert.pem"

echo "Certificates generated successfully in $CERT_DIR"
echo ""
echo "Files created:"
echo "  - ca-cert.pem (CA certificate)"
echo "  - server-cert.pem (Server certificate)"
echo "  - server-key.pem (Server private key)"
echo ""
echo "To connect with MySQL client using caching_sha2_password:"
echo "  mysql -h 127.0.0.1 -u root -p --ssl-ca=$CERT_DIR/ca-cert.pem"
