#!/bin/bash
# Initialize Dolt user with caching_sha2_password authentication
# Run this once after starting the Dolt container for the first time

set -e

echo "Configuring Dolt root user for caching_sha2_password authentication..."

# Wait for Dolt to be ready
until docker exec truth_engine_dolt dolt version > /dev/null 2>&1; do
    echo "Waiting for Dolt to be ready..."
    sleep 2
done

# Check current authentication method for localhost
CURRENT_PLUGIN=$(docker exec truth_engine_dolt dolt sql -q "SELECT plugin FROM mysql.user WHERE user='root' AND host='localhost';" -r csv | tail -n 1)

if [ "$CURRENT_PLUGIN" != "caching_sha2_password" ]; then
    echo "Updating root@localhost to use caching_sha2_password..."
    docker exec truth_engine_dolt dolt sql -q "ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password BY 'password';"
    echo "✓ Root@localhost updated successfully"
else
    echo "✓ Root@localhost already configured with caching_sha2_password"
fi

# Check if root@% exists and configure it
REMOTE_USER_EXISTS=$(docker exec truth_engine_dolt dolt sql -q "SELECT COUNT(*) as count FROM mysql.user WHERE user='root' AND host='%';" -r csv | tail -n 1)

if [ "$REMOTE_USER_EXISTS" = "0" ]; then
    echo "Creating root@% for remote connections..."
    docker exec truth_engine_dolt dolt sql -q "CREATE USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY 'password';"
    docker exec truth_engine_dolt dolt sql -q "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;"
    echo "✓ Root@% created successfully"
else
    # Update existing root@% to use caching_sha2_password
    REMOTE_PLUGIN=$(docker exec truth_engine_dolt dolt sql -q "SELECT plugin FROM mysql.user WHERE user='root' AND host='%';" -r csv | tail -n 1)
    if [ "$REMOTE_PLUGIN" != "caching_sha2_password" ]; then
        echo "Updating root@% to use caching_sha2_password..."
        docker exec truth_engine_dolt dolt sql -q "ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY 'password';"
        echo "✓ Root@% updated successfully"
    else
        echo "✓ Root@% already configured with caching_sha2_password"
    fi
fi

# Apply changes
docker exec truth_engine_dolt dolt sql -q "FLUSH PRIVILEGES;"

echo ""
echo "Dolt is ready for connections with caching_sha2_password authentication"
