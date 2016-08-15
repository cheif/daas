import json
import jinja2
import docker
import socket
import threading
import web
import logging
import itertools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from collections import defaultdict
from os import environ
from subprocess import call

c = docker.Client(base_url='unix://var/run/docker.sock')


def get_aliases(container_id):
    networks = c.inspect_container(container_id)['NetworkSettings']['Networks']
    aliases = [a for a in networks['daas']['Aliases'] or []
               if not container_id.startswith(a)]
    return aliases


def get_aliases_for_network(network_name):
    containers = c.inspect_network(network_name)['Containers']
    aliases_list = map(get_aliases, containers)
    return [a for l in aliases_list for a in l]


def generate_certs_for_network(network_name):
    aliases = get_aliases_for_network(network_name)
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
    # Don't add routes for this container or the registry
    non_routed_aliases = ['daas', 'registry']
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

    # Update nginx conf
    change_nginx_conf()
    call('nginx -s reload', shell=True)
    logging.info('Certificates generated')


def change_nginx_conf(setup=False):
    template = jinja2.Template(open('nginx.tmpl').read())
    env = dict(environ)
    env['setup'] = setup
    nginx_conf = template.render(env=env)
    with open('/etc/nginx/nginx.conf', 'w') as f:
        f.write(nginx_conf)
    call('nginx -s reload', shell=True)


def setup_network(network_name):
    '''Setup a network, and add this container to it'''
    container_id = socket.gethostname()
    container = c.inspect_container(container_id)
    try:
        network_info = c.inspect_network(network_name)
        if container['Id'] not in network_info['Containers']:
            c.connect_container_to_network(container_id, network_name,
                                           aliases=['daas'])
    except docker.errors.NotFound:
        # Create network
        c.create_network(network_name)
        c.connect_container_to_network(container_id, network_name)


def get_containers_with_alias(network_name, alias):
    containers = c.inspect_network(network_name)['Containers']
    aliases_map = defaultdict(list)
    for container in containers:
        for a in get_aliases(container):
            aliases_map[a].append(c.inspect_container(container))

    return aliases_map.get(alias)


def update_container(network_name, repo, tag, alias=None):
    # Try to find an existing container
    alias = alias or repo
    old_containers = get_containers_with_alias(network_name, alias)
    env = []
    if old_containers:
        env = c.inspect_container(old_containers[0])['Config']['Env']
        old_img = c.inspect_container(old_containers[0])['Image']
        new_img = c.inspect_image('{}:{}'.format(repo, tag))['Id']
        if old_img == new_img:
            # Same image, just let the old one run
            return

    new_container = c.create_container('{}:{}'.format(repo, tag),
                                       environment=env)
    c.connect_container_to_network(new_container, network_name,
                                   aliases=[alias])
    c.start(new_container)
    if old_containers:
        for container in old_containers:
            c.stop(container)
            c.remove_container(container)


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


def setup_registry(network_name):
    '''Setup a registry in network'''
    g = c.build(fileobj=open('registry.dockerfile'), tag='registry:notifs')
    for l in g:
        # It seems like we'll have to interate through the generator for the
        # build to happen
        pass
    update_container(network_name, 'registry', 'notifs')


class EventHandler(object):
    def POST(self):
        d = json.loads(web.data())
        for e in d['events']:
            if e['action'] == 'push' and 'tag' in e['target']:
                repo_name, tag = e['target']['repository'], e['target']['tag']
                repo = '{}/{}'.format(environ['DOMAIN_NAME'], repo_name)
                c.pull('{}:{}'.format(repo, tag))
                update_container(self.network_name, repo, tag, alias=repo_name)


class ConfigHandler(object):
    def GET(self, alias):
        def _get_info(cont):
            cont = c.inspect_container(cont)
            network_info = \
                cont['NetworkSettings']['Networks'][self.network_name]
            alias = network_info['Aliases'][0] if network_info['Aliases'] \
                else ''
            return {
                'alias': alias,
                'env': cont['Config']['Env'],
                'state': 'running' if cont['State']['Running'] else 'error',
            }
        container_info = [_get_info(cont) for cont in c.containers()]
        return json.dumps(container_info)

    def PUT(self, alias):
        d = json.loads(web.data())
        env = [e for e in d['env'] if e != '']
        update_environment(self.network_name, alias, env)

    def POST(self):
        d = json.loads(web.data())
        for e in d['events']:
            if e['action'] == 'push' and 'tag' in e['target']:
                repo_name, tag = e['target']['repository'], e['target']['tag']
                repo = '{}/{}'.format(environ['DOMAIN_NAME'], repo_name)
                c.pull('{}:{}'.format(repo, tag))
                update_container(self.network_name, repo, tag, alias=repo_name)


class IndexHandler(object):
    def GET(self):
        return web.template.render('').index()


app = web.application((
    '/', 'IndexHandler',
    '/events', 'EventHandler',
    '/config(?:/(?P<alias>[^/]*))?/?', 'ConfigHandler'),
    globals())


def start_event_listener(network_name):
    # Ugly way to pass network_name to web-handler
    EventHandler.network_name = network_name
    ConfigHandler.network_name = network_name
    thread = threading.Thread(target=app.run)
    thread.daemon = True
    thread.start()
    logging.info("HTTP started")


def main():
    network_name = environ.get('NETWORK_NAME', 'daas')
    setup_network(network_name)

    start_event_listener(network_name)

    logging.info("Network fixed")

    setup_registry(network_name)
    logging.info("Registry running")

    # Create a setup-nginx, that can be used for letsencrypt on first run
    call('nginx -s stop', shell=True)
    change_nginx_conf(setup=True)
    call('nginx', shell=True)

    generate_certs_for_network(network_name)

    c.login(environ.get('USERNAME'), environ.get('PASSWORD'),
            registry=environ.get('DOMAIN_NAME'))
    for ev in c.events(filters={'network': network_name}):
        generate_certs_for_network(network_name)


if __name__ == '__main__':
    main()
