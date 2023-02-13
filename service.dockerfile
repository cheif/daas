FROM alpine:3.15.0
RUN apk add --no-cache nginx-mod-stream certbot py-pbr py-funcsigs openssl bash py3-jinja2 py3-pip supercronic
RUN pip install docker web.py zope.hookable zope.deprecation zope.deferredimport
RUN mkdir /var/www/letsencrypt /etc/nginx/conf.d

ENTRYPOINT ["/entrypoint.sh"]
WORKDIR /usr/src/app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY . .

LABEL auth=password

CMD ["./run.sh"]
