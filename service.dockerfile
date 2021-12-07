FROM alpine:3.15.0
# RUN echo 'http://dl-cdn.alpinelinux.org/alpine/edge/testing' >> /etc/apk/repositories
RUN apk add --no-cache nginx-mod-stream certbot py-pbr py-funcsigs openssl bash py3-jinja2 py3-pip
# py-pbr and py-funcsigs are required by certbot, but not installed
RUN pip install docker web.py zope.hookable zope.deprecation zope.deferredimport
RUN mkdir /var/www/letsencrypt /etc/nginx/conf.d

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "watch.py"]

WORKDIR /usr/src/app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY . .
