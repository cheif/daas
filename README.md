# Docker-based PaaS

DaaS is a small docker-based PaaS that you can run yourself, to get up and running you'll need a server with docker installed, and a hostname pointing to the server.

## Setup

To start it you should be able to run:

```
docker run -p 80:80 -p 443:443 -e DOMAIN_NAME=example.com -e USERNAME=foo -e PASSWORD=bar -v /var/run/docker.sock:/var/run/docker.sock -d --restart=always cheif/daas
```

This should pull the image from docker hub and start it up, the port forwards is for http(s) traffic, the DOMAIN_NAME env is for setting up SSL with Let's encrypt and the mounting of the `docker.sock` is to be able to start new containers etc.

USERNAME/PASSWORD will be used with basic auth both for the web-interface (reachable at `example.com/` and for the docker registry.

## Using

When the DaaS-container is running on the server it should have started a docker-registry, so to get your project up and running on it you should be able to tag and push an image.

```
docker build -t example.com/foobar .
docker push example.com/foobar
```

When the image has been pushed to DaaS it should be started and accessible at `https://foobar.example.com`. If you have any problems you should probably look at the logs from the DaaS-container.

If the container defines ports (e.g. by using `EXPOSE 3000`) that port will be used (AKA mapped to http(s)), otherwise we fallback to port 8080.

# How does it work

It's a pretty simple setup really, we have a script, `main.py` that does two things:

1. Listens on pushes to the docker-registry, and starts a container with a --net-alias of the image-name, `foobar` in the example above, and shuts any old containers with tha alias down (thus rolling updates, but no guarantees).

2. Listens for new containers added to the specified docker-network (`daas` by default), adds nginx-routes for that container and requests new certificates from Let's encrypt when needed. This step is neccesarry since we can't get wildcard certificates from Let's encrypt.

The actual routing is done in nginx, so step 2 above just generates a nginx.conf.

# Further work
- [ ] It might make sense to use `docker swarm` intsead of this very custom solution, but that was released long after this was written, so the effort to port might not be worth it.
