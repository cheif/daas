FROM registry:2.7.1
# Add a notification-statement to the config
RUN printf '''\n\
notifications:\n\
  endpoints:\n\
    - name: daas-webhook\n\
      url: http://daas:8080/events\n\
      headers:\n\
      timeout: 600000ms\n\
      threshold: 5\n\
      backoff: 1s\n\
''' >> /etc/docker/registry/config.yml
