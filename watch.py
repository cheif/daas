import json
import jinja2
import docker
import socket
import threading
import web
import logging
import itertools
import time
from collections import defaultdict
from os import environ
from subprocess import call

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

c = docker.DockerClient(base_url='unix://var/run/docker.sock')


def get_aliases(container):
    network = container.attrs['NetworkSettings']['Networks'].get('daas', {})
    aliases = [a for a in network.get('Aliases', {})
               if not container.id.startswith(a)]
    return aliases


def generate_certs_for_network(network):
    aliases_list = map(get_aliases, network.containers)
    aliases = [a for l in aliases_list for a in l]
    generate_certs_and_restart_nginx(aliases)


def get_current_domains():
    '''Fetch current domains we have a cert for from renew-conf'''
    path = '/etc/letsencrypt/renewal/{}.conf'.format(environ['DOMAIN_NAME'])
    try:
        with open(path) as f:
            lines = f.readlines()
            lines = list(itertools.dropwhile(lambda l: 'webroot_map' not in l,
                                             lines))[1:]
            return [l.split('=')[0].strip() for l in lines]
    except IOError:
        return []


def generate_certs_and_restart_nginx(aliases):
    if 'DOMAIN_NAME' in environ:
        # Don't add routes for this container or the registry
        non_routed_aliases = ['registry']
        domain = environ['DOMAIN_NAME']
        fqdns = {domain}
        fqdns.update(set(['{}.{}'.format(a, domain) for a in aliases
                          if a not in non_routed_aliases]))
        fqdns.update(get_current_domains())
        cmd = 'certbot certonly --webroot --agree-tos --expand --email=admin@{} \
    --non-interactive -w /var/www/letsencrypt '.format(domain)
        cmd += ' '.join(['-d {}'.format(fqdn) for fqdn in fqdns])

        # Run certbot
        logging.info('Generating certs for: {}'.format(fqdns))
        call(cmd, shell=True)
        logging.info('Certificates generated')


def change_nginx_conf():
    template = jinja2.Template(open('nginx.tmpl').read())
    env = dict(environ)
    nginx_conf = template.render(env=env)
    with open('/etc/nginx/nginx.conf', 'w') as f:
        f.write(nginx_conf)
    call('nginx -s reload', shell=True)
    logging.info('nginx.conf updated')


def setup_network(network_name):
    '''Setup a network, and add this container to it'''
    container_id = socket.gethostname()
    container = c.containers.get(container_id)
    try:
        network = c.networks.get(network_name)
    except docker.errors.NotFound:
        # Create network
        network = c.network.create(network_name)
    if container not in network.containers:
        network.connect(container, aliases=['daas'])

    return network


def get_containers_with_alias(network, alias):
    aliases_map = defaultdict(list)
    for container in network.containers:
        for a in get_aliases(container):
            aliases_map[a].append(container)

    return aliases_map.get(alias)


def update_container(network_name, repo, tag, alias=None):
    # Try to find an existing container
    network = c.networks.get(network_name)
    alias = alias or repo
    image_name = '{}:{}'.format(repo, tag)
    old_containers = get_containers_with_alias(network, alias)
    env = []
    if old_containers:
        env = old_containers[0].attrs['Config']['Env']
        old_img = old_containers[0].image
        new_img = c.images.get(image_name)
        if old_img.id == new_img.id:
            # Same image, just let the old one run
            return

    try:
        volumes = list(
            (c.images.get(image_name).attrs['Config']['Volumes'] or {}).keys()
        )
        volume_config = [
            '{}-{}-{}-{}:{}'.format(environ['DOMAIN_NAME'], alias, tag,
                                    vol.replace('/', '_'), vol)
            for vol in volumes
        ]

        new_container = c.containers.create('{}:{}'.format(repo, tag),
                                            environment=env,
                                            volumes=volume_config)
    except docker.errors.NotFound as e:
        logging.error(e)
        # Just abort for now
        return
    network.connect(new_container, aliases=[alias])
    new_container.start()
    if old_containers:
        for container in old_containers:
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
    g = c.images.build(fileobj=open('registry.dockerfile', mode='rb'),
                       tag='registry:notifs')
    for l in g:
        # It seems like we'll have to interate through the generator for the
        # build to happen
        pass
    update_container(network.name, 'registry', 'notifs')
    logging.info("Registry running")


class EventHandler(object):
    def POST(self):
        d = json.loads(web.data())
        for e in d['events']:
            if e['action'] == 'push' and 'tag' in e['target']:
                repo_name, tag = e['target']['repository'], e['target']['tag']
                repo = '{}/{}'.format(environ['DOMAIN_NAME'], repo_name)
                for line in c.pull(repository=repo, tag=tag, stream=True):
                    logging.info(json.dumps(json.loads(line), indent=4))
                update_container(self.network_name, repo, tag, alias=repo_name)


class ConfigHandler(object):
    def GET(self, alias):
        def _get_info(cont):
            aliases = get_aliases(cont)
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
    globals())


def start_event_listener(network):
    # Ugly way to pass network_name to web-handler
    EventHandler.network_name = network.name
    ConfigHandler.network_name = network.name
    thread = threading.Thread(target=app.run)
    thread.daemon = True
    thread.start()
    logging.info("HTTP started")


def main():
    network_name = environ.get('NETWORK_NAME', 'daas')
    network = setup_network(network_name)

    start_event_listener(network)

    logging.info("Network fixed")

    setup_registry(network)

    # Create a setup-nginx, that can be used for letsencrypt on first run
    call('nginx -s stop', shell=True)
    change_nginx_conf()
    call('nginx', shell=True)

    generate_certs_for_network(network)

    # Make sure everything is up
    time.sleep(1)

    if 'DOMAIN_NAME' in environ:
        c.login(environ.get('USERNAME'), environ.get('PASSWORD'),
                registry=environ.get('DOMAIN_NAME'))
    for ev in c.events(filters={'network': network_name}):
        generate_certs_for_network(network)


if __name__ == '__main__':
    main()
