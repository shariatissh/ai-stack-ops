#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AiStackOps MCP Server (Model Context Protocol - stdio JSON-RPC 2.0)
Connects Claude Desktop, Claude Code, OpenCode, Cursor, Windsurf, and ChatGPT
directly to any user's OpenStack infrastructure with built-in PREVIEW_ONLY safety.
Zero external dependencies required (uses osctl directly).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import osctl

SERVER_NAME = "aistackops-mcp"
SERVER_VERSION = "1.0.0"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "openstack_overview",
        "description": "Get a complete overview of the OpenStack project (servers, security groups, floating IPs, volumes, and status summary).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "openstack_servers_list",
        "description": "List all OpenStack virtual machines (servers) with their IPs, status, flavor, and attached security groups.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "openstack_server_inspect",
        "description": "Inspect a specific server (by name, partial name, IP, or ID) and list all its network ports and attached Security Groups.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Server name, IP address, or ID"},
            },
            "required": ["server"],
        },
    },
    {
        "name": "openstack_server_action",
        "description": "Perform a power/lifecycle action on a server (start, stop, reboot-soft, reboot-hard, pause, unpause, suspend, resume). Always call with confirm=false first to preview and ask user confirmation!",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Server name, IP, or ID"},
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "reboot-soft", "reboot-hard", "pause", "unpause", "suspend", "resume"],
                },
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Set to true ONLY after the user explicitly confirms the previewed action.",
                },
            },
            "required": ["server", "action"],
        },
    },
    {
        "name": "openstack_sg_list",
        "description": "List all Security Groups in the OpenStack project and the servers attached to each.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "with_rules": {"type": "boolean", "default": False, "description": "Include full firewall rules"},
            },
        },
    },
    {
        "name": "openstack_sg_check_access",
        "description": "Check whether an IP or CIDR currently has firewall access to a specific server on a given port/protocol.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Server name, IP, or ID"},
                "ip": {"type": "string", "description": "IP address or CIDR to check"},
                "port": {"type": "string", "default": "all", "description": "Port number or service name (ssh, rdp, web, etc.)"},
                "protocol": {"type": "string", "default": "tcp", "enum": ["tcp", "udp", "icmp", "any"]},
            },
            "required": ["server", "ip"],
        },
    },
    {
        "name": "openstack_sg_allow",
        "description": "Allow an IP/CIDR in an OpenStack Security Group. Always run with confirm=false first, ask the user which Security Group and port they want, and only set confirm=true after explicit user approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sg": {"type": "string", "description": "Target Security Group name or ID"},
                "server": {"type": "string", "description": "Optional server name/ID to verify the SG is attached to it"},
                "ip": {"type": "string", "description": "IP address or CIDR (e.g. 185.10.20.30 or 192.168.1.0/24)"},
                "port": {
                    "type": "string",
                    "default": "all",
                    "description": "Port, range, or service alias (ssh, rdp, http, https, web, mysql, postgres, redis, ping, all)",
                },
                "protocol": {"type": "string", "default": "tcp", "enum": ["tcp", "udp", "icmp", "ping", "any"]},
                "description": {"type": "string", "default": "", "description": "Rule description"},
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Set to true ONLY after the user explicitly confirms.",
                },
            },
            "required": ["sg", "ip"],
        },
    },
    {
        "name": "openstack_sg_revoke",
        "description": "Revoke/delete a firewall rule from a Security Group by rule_id or by (sg + ip). Always preview with confirm=false first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "Exact Security Group Rule ID"},
                "sg": {"type": "string", "description": "Security Group name or ID"},
                "server": {"type": "string", "description": "Optional server name/ID"},
                "ip": {"type": "string", "description": "IP or CIDR to revoke"},
                "port": {"type": "string", "description": "Optional port filter"},
                "protocol": {"type": "string", "description": "Optional protocol filter"},
                "confirm": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "openstack_backup",
        "description": "Create a full snapshot backup of all Security Groups and server attachments before making changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "default": "before_changes"},
            },
        },
    },
    {
        "name": "openstack_revert",
        "description": "Revert all Security Group rules and server attachments back to the latest backup snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "default": ""},
                "confirm": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "openstack_sg_audit",
        "description": "AI Security Auditor: Scan all Security Groups for risky 0.0.0.0/0 public exposures (SSH, RDP, DBs, all ports), duplicate rules, and unused Security Groups.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "openstack_server_diagnose",
        "description": "AI Server Diagnostics: Inspect server health, power/task state, network ports, floating IPs, and active ingress firewall rules to troubleshoot connectivity or boot issues.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Server name, IP, or ID"},
            },
            "required": ["server"],
        },
    },
    {
        "name": "openstack_quotas",
        "description": "Get OpenStack project compute (CPU, RAM, instances) and volume storage quotas and current usage.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "openstack_volumes_list",
        "description": "List all Cinder volumes (disks) and the servers they are attached to.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "openstack_fip_list",
        "description": "List all Floating IPs (public IPs) and their associated servers.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "openstack_networks_list",
        "description": "List all Neutron networks and subnets in the project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def dispatch_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    config = osctl.load_config()
    client = osctl.OpenStackClient(config)

    if name == "openstack_overview":
        return osctl.cmd_overview(client, argparse.Namespace())
    if name == "openstack_servers_list":
        return osctl.cmd_servers_list(client, argparse.Namespace())
    if name == "openstack_server_inspect":
        return osctl.cmd_sg_for_server(client, argparse.Namespace(server=arguments["server"]))
    if name == "openstack_server_action":
        return osctl.cmd_servers_action(
            client,
            argparse.Namespace(
                server=arguments["server"],
                action_type=arguments["action"],
                confirm=bool(arguments.get("confirm", False)),
            ),
        )
    if name == "openstack_sg_list":
        return osctl.cmd_sg_list(client, argparse.Namespace(with_rules=bool(arguments.get("with_rules", False))))
    if name == "openstack_sg_check_access":
        return osctl.cmd_sg_check_access(
            client,
            argparse.Namespace(
                server=arguments["server"],
                ip=arguments["ip"],
                port=arguments.get("port", "all"),
                protocol=arguments.get("protocol", "tcp"),
                direction="ingress",
            ),
        )
    if name == "openstack_sg_allow":
        return osctl.cmd_sg_allow(
            client,
            argparse.Namespace(
                sg=arguments["sg"],
                server=arguments.get("server"),
                ip=arguments["ip"],
                port=arguments.get("port", "all"),
                protocol=arguments.get("protocol", "tcp"),
                direction="ingress",
                description=arguments.get("description", ""),
                confirm=bool(arguments.get("confirm", False)),
            ),
        )
    if name == "openstack_sg_revoke":
        return osctl.cmd_sg_revoke(
            client,
            argparse.Namespace(
                rule_id=arguments.get("rule_id"),
                sg=arguments.get("sg"),
                server=arguments.get("server"),
                ip=arguments.get("ip"),
                port=arguments.get("port"),
                protocol=arguments.get("protocol"),
                confirm=bool(arguments.get("confirm", False)),
            ),
        )
    if name == "openstack_backup":
        return osctl.cmd_backup_create(client, argparse.Namespace(label=arguments.get("label", "before_changes")))
    if name == "openstack_revert":
        return osctl.cmd_revert(
            client,
            argparse.Namespace(file=arguments.get("file", ""), confirm=bool(arguments.get("confirm", False))),
        )
    if name == "openstack_sg_audit":
        return osctl.cmd_sg_audit(client, argparse.Namespace())
    if name == "openstack_server_diagnose":
        return osctl.cmd_servers_diagnose(client, argparse.Namespace(server=arguments["server"]))
    if name == "openstack_quotas":
        return osctl.cmd_quotas_show(client, argparse.Namespace())
    if name == "openstack_volumes_list":
        return osctl.cmd_volumes_list(client, argparse.Namespace())
    if name == "openstack_fip_list":
        return osctl.cmd_fip_list(client, argparse.Namespace())
    if name == "openstack_networks_list":
        return osctl.cmd_network_list(client, argparse.Namespace())

    raise ValueError(f"Unknown tool: {name}")


def handle_request(req: Dict[str, Any]) -> Dict[str, Any] | None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            res = dispatch_tool(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
                    "isError": False,
                },
            }
        except osctl.OpenStackError as e:
            err = {"ok": False, "error_code": e.code, "message": e.message, "details": e.details}
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(err, ensure_ascii=False, indent=2)}],
                    "isError": True,
                },
            }
        except Exception as e:
            err = {"ok": False, "error_code": "MCP_TOOL_ERROR", "message": str(e)}
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(err, ensure_ascii=False, indent=2)}],
                    "isError": True,
                },
            }

    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
