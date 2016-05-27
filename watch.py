import os
import jinja2
import docker
import socket
from subprocess import call

c = docker.Client(base_url='unix://var/run/docker.sock')


def get_aliases(container_id):
    networks = c.inspect_container(container_id)['NetworkSettings']['Networks']
    aliases = networks['daas']['Aliases']
    return aliases or []


def get_aliases_for_network(network_name):
    containers = c.inspect_network(network_name)['Containers']
    aliases_list = map(get_aliases, containers)
    return [a for l in aliases_list for a in l]


def generate_certs_for_network(network_name):
    aliases = get_aliases_for_network(network_name)
    generate_certs_and_restart_nginx(aliases)


def generate_certs_and_restart_nginx(aliases):
    domain = os.environ['DOMAIN_NAME']
    fqdns = [domain]
    fqdns += ['{}.{}'.format(a, domain) for a in aliases]
    cmd = 'certbot certonly --webroot --agree-tos --expand --email=admin@{} \
--non-interactive -w /var/www/letsencrypt '.format(domain)
    cmd += ' '.join(['-d {}'.format(fqdn) for fqdn in fqdns])
    print 'Generating certs for: {}'.format(fqdns)

    # Run certbot
    call(cmd, shell=True)

    # Update nginx conf
    change_nginx_conf()
    call('nginx -s reload', shell=True)
    print 'Certificates generated'


def change_nginx_conf(setup=False):
    template = jinja2.Template(open('nginx.tmpl').read())
    env = dict(os.environ)
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
            c.connect_container_to_network(container_id, network_name)
    except docker.errors.NotFound:
        # Create network
        c.create_network(network_name)
        c.connect_container_to_network(container_id, network_name)


def main():
    # Create a setup-nginx, that can be used for letsencrypt on first run
    network_name = os.environ.get('NETWORK_NAME', 'daas')
    setup_network(network_name)
    change_nginx_conf(setup=True)
    call('nginx', shell=True)
    generate_certs_for_network(network_name)
    for ev in c.events(filters={'network': network_name}):
        generate_certs_for_network(network_name)


if __name__ == '__main__':
    main()
