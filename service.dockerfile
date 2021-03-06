FROM alpine:3.10.1
# RUN echo 'http://dl-cdn.alpinelinux.org/alpine/edge/testing' >> /etc/apk/repositories
RUN apk add --no-cache nginx-mod-stream certbot py-pbr py-funcsigs openssl bash py-jinja2 py2-pip
# py-pbr and py-funcsigs are required by certbot, but not installed
RUN pip install docker-py web.py zope.hookable zope.deprecation zope.deferredimport
RUN mkdir /var/www/letsencrypt /run/nginx

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "watch.py"]

WORKDIR /usr/src/app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY . .
