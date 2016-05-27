#!/bin/bash
set -e

if [ ! -f /etc/letsencrypt/dhparam.pem ]; then
    # Setup dhparam for strong cipher
    openssl dhparam -out /etc/letsencrypt/dhparam.pem 2048
fi

exec "$@"
