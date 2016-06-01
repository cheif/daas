#!/bin/bash
set -e

if [ ! -f /etc/letsencrypt/dhparam.pem ]; then
    # Setup dhparam for strong cipher
    openssl dhparam -out /etc/letsencrypt/dhparam.pem 2048
fi

printf "$USERNAME:$(openssl passwd -crypt $PASSWORD)\n" > /etc/nginx/conf.d/nginx.htpasswd

exec "$@"
