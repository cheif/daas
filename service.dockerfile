FROM alpine:edge
# RUN echo 'http://dl-cdn.alpinelinux.org/alpine/edge/testing' >> /etc/apk/repositories
RUN apk add --no-cache nginx certbot py-pbr py-funcsigs openssl bash py-jinja2 py-pip
# py-pbr and py-funcsigs are required by certbot, but not installed
RUN pip install docker-py web.py
WORKDIR /usr/src/app
COPY nginx.tmpl nginx.tmpl
COPY watch.py watch.py
COPY registry.dockerfile registry.dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN mkdir /var/www/letsencrypt /run/nginx
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "watch.py"]
