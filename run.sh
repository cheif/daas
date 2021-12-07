#!/bin/bash
supercronic renew.crontab &

python3 main.py --watch
