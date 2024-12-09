import asyncio
from configparser import ConfigParser
import os
from typing import Any
import shlex

import yaml
import requests


async def wait_coroutine(*args, **kwargs) -> int:
    coro = await asyncio.create_subprocess_exec(*args, **kwargs)
    result = await coro.wait()
    return result


def read_bitswan_yaml(bitswan_dir: str) -> dict[str, Any] | None:
    bitswan_yaml_path = os.path.join(bitswan_dir, "bitswan.yaml")
    try:
        if os.path.exists(bitswan_yaml_path):
            with open(bitswan_yaml_path, "r") as f:
                bs_yaml: dict = yaml.safe_load(f)
                return bs_yaml
    except Exception:
        return None


async def call_git_command(*command, **kwargs) -> bool:
    host_dir = os.environ.get(
        "BITSWAN_GITOPS_DIR_HOST", "/home/root/.config/bitswan/local-gitops/"
    )
    host_path = os.environ.get("HOST_PATH")
    host_home = os.environ.get("HOST_HOME")
    host_user = os.environ.get("HOST_USER")

    # If all host environment variables are set, use nsenter to run git command on host
    if host_dir and host_path and host_home and host_user:
        host_command = 'PATH={} su - {} -c "cd {} && PATH={} HOME={} {}"'.format(
            host_path,
            host_user,
            host_dir,
            host_path,
            host_home,
            " ".join(shlex.quote(arg) for arg in command),
        )
        nsenter_command = [
            "nsenter",
            "-t",
            "1",
            "-m",
            "-u",
            "-n",
            "-i",
            "sh",
            "-c",
            host_command,
        ]
        result = await wait_coroutine(*nsenter_command)
        return result == 0

    # Fallback to local git command
    result = await wait_coroutine(*command, **kwargs)
    return result == 0


def read_pipeline_conf(source_dir: str) -> ConfigParser | None:
    conf_file_path = os.path.join(source_dir, "pipelines.conf")
    if os.path.exists(conf_file_path):
        config = ConfigParser()
        config.read(conf_file_path)
        return config
    return None


def add_route_to_caddy(deployment_id: str, port: str) -> bool:
    caddy_url = os.environ.get("CADDY_URL", "http://caddy:2019")
    upstreams = requests.get(f"{caddy_url}/reverse_proxy/upstreams")
    gitops_domain = os.environ.get("BITSWAN_GITOPS_DOMAIN", "gitops.bitswan.space")

    if upstreams.status_code != 200:
        return False

    upstreams = upstreams.json()
    for upstream in upstreams:
        name = upstream.get("address").split(":")[0]
        # deployment_id is already in the upstreams
        if name == deployment_id:
            return True

    body = [
        {
            "match": [{"host": ["{}.{}".format(deployment_id, gitops_domain)]}],
            "handle": [
                {
                    "handler": "subroute",
                    "routes": [
                        {
                            "handle": [
                                {
                                    "handler": "reverse_proxy",
                                    "upstreams": [
                                        {"dial": "{}:{}".format(deployment_id, port)}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ],
            "terminal": True,
        }
    ]

    routes_url = "{}/config/apps/http/servers/srv0/routes/...".format(caddy_url)
    response = requests.post(routes_url, json=body)
    return response.status_code == 200
