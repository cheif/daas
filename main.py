import json
import jinja2
import docker
import socket
import threading
import web
import logging
import itertools
import sys
import getopt
from os import environ
from subprocess import call

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

c = docker.DockerClient(base_url='unix://var/run/docker.sock')


def get_current_domains():
    '''Fetch current domains we have a cert for from renew-conf'''
    path = '/etc/letsencrypt/renewal/{}.conf'.format(environ['DOMAIN_NAME'])
    try:
        with open(path) as f:
            lines = f.readlines()
            lines = list(itertools.dropwhile(lambda l: 'webroot_map' not in l,
                                             lines))[1:]
            return [line.split('=')[0].strip() for line in lines]
    except IOError:
        return []


def generate_certs_for_aliases(aliases, domain):
    fqdns = {domain}
    fqdns.update(set(['{}.{}'.format(a, domain) for a in aliases]))
    fqdns.update(get_current_domains())
    cmd = 'certbot certonly --webroot --agree-tos --expand --email=admin@{} \
--non-interactive -w /var/www/letsencrypt '.format(domain)
    cmd += ' '.join(['-d {}'.format(fqdn) for fqdn in fqdns])

    # Run certbot
    logging.info('Generating certs for: {}'.format(fqdns))
    call(cmd, shell=True)
    logging.info('Certificates generated')


def get_aliases(container, network):
    network_settings = container.attrs['NetworkSettings']
    return [alias for alias in
            network_settings['Networks']
            .get(network.name, {}).get('Aliases', [])
            if not container.id.startswith(alias)]


def get_routes(container, network):
    ports = [key.split('/')[0] for key, value in
             container.attrs['NetworkSettings']['Ports'].items()
             if value is None]
    non_routed_aliases = ['registry']
    aliases = [alias for alias in get_aliases(container, network)
               if alias not in non_routed_aliases]
    logging.info('Getting routes for: {}, aliases: {}, ports: {}'.format(
        container, aliases, ports
    ))
    if len(aliases) > 0:
        return {
            "alias": aliases[0],
            "port": ports[0] if len(ports) else '8080'
        }


def update_nginx_conf(network, use_certificates=True):
    routes = [v for v in [get_routes(container, network)
                          for container in network.containers]
              if v is not None]
    template = jinja2.Template(open('nginx.tmpl').read(),
                               trim_blocks=True,
                               lstrip_blocks=True)
    env = dict(environ)
    nginx_conf = template.render(
        env=env,
        routes=routes,
        use_certificates=use_certificates
    )
    with open('/etc/nginx/nginx.conf', 'w') as f:
        f.write(nginx_conf)
    logging.info('nginx.conf updated')
    return [r['alias'] for r in routes]


def setup_network(network_name):
    '''Setup a network, and add this container to it'''
    container_id = socket.gethostname()
    container = c.containers.get(container_id)
    try:
        network = c.networks.get(network_name)
    except docker.errors.NotFound:
        # Create network
        network = c.networks.create(network_name)
    if container not in network.containers:
        network.connect(container, aliases=['daas'])

    return network


def get_containers_with_alias(network, alias):
    containers = []
    for container in network.containers:
        if alias in get_aliases(container, network):
            containers.append(container)

    return containers


def update_container(network_name, repo, tag, alias=None):
    # Try to find an existing container
    network = c.networks.get(network_name)
    alias = alias or repo
    image_name = '{}:{}'.format(repo, tag)
    logging.info("Updating: {}".format(image_name))
    old_containers = get_containers_with_alias(network, alias)
    logging.info("Found old containers: {}".format(old_containers))
    env = []
    if old_containers:
        env = old_containers[0].attrs['Config']['Env']
        old_img = old_containers[0].image
        new_img = c.images.get(image_name)
        if old_img.id == new_img.id:
            logging.info("Already running this image")
            # Same image, just let the old one run
            return

    try:
        volumes = list(
            (c.images.get(image_name).attrs['Config']['Volumes'] or {}).keys()
        )
        volume_config = [
            '{}-{}-{}-{}:{}'.format(environ.get('DOMAIN_NAME', 'daas'), alias,
                                    tag, vol.replace('/', '_'), vol)
            for vol in volumes
        ]

        new_container = c.containers.create('{}:{}'.format(repo, tag),
                                            environment=env,
                                            volumes=volume_config)
    except docker.errors.NotFound as e:
        logging.error(e)
        # Just abort for now
        return
    logging.info("Starting new container: {}".format(new_container))
    network.connect(new_container, aliases=[alias])
    new_container.start()
    if old_containers:
        for container in old_containers:
            logging.info("Killing old container: {}".format(container))
            container.stop()
            container.remove()


def update_environment(network_name, alias, env):
    # Find container running with alias, and create a copy with new environment
    containers = get_containers_with_alias(network_name, alias)
    container = containers[0]

    new = c.create_container(container['Image'], environment=env)
    c.connect_container_to_network(new, network_name, aliases=[alias])
    c.start(new)

    for old in containers:
        c.stop(old)
        c.remove_container(old)


def setup_registry(network):
    '''Setup a registry in network'''
    c.images.build(fileobj=open('registry.dockerfile', mode='rb'),
                   tag='registry:notifs')
    update_container(network.name, 'registry', 'notifs')
    logging.info("Registry running")


class EventHandler(object):
    def POST(self):
        d = json.loads(web.data())
        for e in d['events']:
            if e['action'] == 'push' and 'tag' in e['target']:
                repo_name, tag = e['target']['repository'], e['target']['tag']
                repo = '{}/{}'.format(environ['DOMAIN_NAME'], repo_name)
                c.images.pull(repo, tag=tag)
                update_container(self.network_name, repo, tag, alias=repo_name)


class ConfigHandler(object):
    def GET(self, alias):
        def _get_info(cont):
            aliases = get_aliases(cont, c.networks.get(self.network_name))
            return {
                'alias': aliases[0] if len(aliases) else "-MISSING-",
                'env': cont.attrs['Config']['Env'],
                'state': cont.status,
            }
        container_info = [_get_info(cont) for cont in c.containers.list()]
        return json.dumps(container_info)

    def PUT(self, alias):
        d = json.loads(web.data())
        env = [e for e in d['env'] if e != '']
        update_environment(self.network_name, alias, env)


class IndexHandler(object):
    def GET(self):
        return web.template.render('').index()


app = web.application((
    '/', 'IndexHandler',
    '/events', 'EventHandler',
    '/config(?:/(?P<alias>[^/]*))?/?', 'ConfigHandler'),
    locals())


def runwebapp():
    web.httpserver.runsimple(app.wsgifunc(), ('0.0.0.0', 8080))


def start_event_listener(network):
    # Ugly way to pass network_name to web-handler
    EventHandler.network_name = network.name
    ConfigHandler.network_name = network.name
    thread = threading.Thread(target=runwebapp)
    thread.daemon = True
    thread.start()
    logging.info("HTTP started")


def watch(network):
    start_event_listener(network)

    setup_registry(network)

    # First we generate this conf without certificates, since we might not
    # have the certificates on disk yet, and otherwise nginx will fail
    aliases = update_nginx_conf(network, use_certificates=False)
    call('nginx', shell=True)

    if 'DOMAIN_NAME' in environ:
        generate_certs_for_aliases(aliases, environ.get('DOMAIN_NAME'))
        aliases = update_nginx_conf(network)
        call('nginx -s reload', shell=True)

        c.login(environ.get('USERNAME'), environ.get('PASSWORD'),
                registry=environ.get('DOMAIN_NAME'))
    for ev in c.events(decode=True, filters={'network': network_name}):
        logging.info("Got event: {}".format(ev))

        # Make sure network has updated data
        network.reload()
        aliases = update_nginx_conf(network)
        call('nginx -s reload', shell=True)

        if 'DOMAIN_NAME' in environ:
            generate_certs_for_aliases(aliases, environ.get('DOMAIN_NAME'))
            call('nginx -s reload', shell=True)

    call('nginx -s stop', shell=True)


if __name__ == '__main__':
    network_name = environ.get('NETWORK_NAME', 'daas')
    network = setup_network(network_name)

    logging.info("Network fixed")

    opts, args = getopt.getopt(sys.argv[1:], '', ['renew', 'watch'])
    for opt, _ in opts:
        if opt == '--watch':
            watch(network)
        elif opt == '--renew' and 'DOMAIN_NAME' in environ:
            aliases = update_nginx_conf(network)
            generate_certs_for_aliases(aliases, environ.get('DOMAIN_NAME'))
