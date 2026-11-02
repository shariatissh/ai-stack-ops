#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AiStackOps (osctl) - Universal AI-Native OpenStack Cloud Copilot Engine
Supports both Horizon Web API Gateway (HTTPS port 443) and standard OpenStack
configurations (.env, openrc environment variables, clouds.yaml, and multi-profile setups).
Never exposes secrets in outputs.
"""

import argparse
import datetime
import hashlib
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3

# Suppress InsecureRequestWarning when OS_VERIFY_SSL=false
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent
USER_CONFIG_DIR = Path.home() / ".config" / "aistackops"
DEFAULT_ENV_PATH = BASE_DIR / ".env" if (BASE_DIR / ".env").exists() else (Path.cwd() / ".env" if (Path.cwd() / ".env").exists() else USER_CONFIG_DIR / ".env")
AUDIT_LOG_PATH = BASE_DIR / "audit.log" if os.access(BASE_DIR, os.W_OK) else USER_CONFIG_DIR / "audit.log"
BACKUPS_DIR = BASE_DIR / "backups" if os.access(BASE_DIR, os.W_OK) else USER_CONFIG_DIR / "backups"
TOKEN_CACHE_DIR = Path("/tmp/opencode") if Path("/tmp/opencode").exists() else Path("/tmp")
TOKEN_CACHE_PATH = TOKEN_CACHE_DIR / ".os_token_cache.json"

SERVICE_PORT_ALIASES: Dict[str, Tuple[str, str]] = {
    "ssh": ("tcp", "22"),
    "http": ("tcp", "80"),
    "https": ("tcp", "443"),
    "web": ("tcp", "80,443"),
    "rdp": ("tcp", "3389"),
    "mysql": ("tcp", "3306"),
    "mariadb": ("tcp", "3306"),
    "postgres": ("tcp", "5432"),
    "postgresql": ("tcp", "5432"),
    "mssql": ("tcp", "1433"),
    "redis": ("tcp", "6379"),
    "mongodb": ("tcp", "27017"),
    "mongo": ("tcp", "27017"),
    "elasticsearch": ("tcp", "9200"),
    "rabbitmq": ("tcp", "5672,15672"),
    "winrm": ("tcp", "5985,5986"),
    "ftp": ("tcp", "21"),
    "dns": ("udp", "53"),
    "ping": ("icmp", "all"),
    "icmp": ("icmp", "all"),
}


class OpenStackError(Exception):
    def __init__(self, message: str, code: str = "ERROR", details: Any = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details
        self.status_code = status_code


def parse_dotenv(env_path: Path) -> Dict[str, str]:
    """Safely parse a .env file without external dependencies."""
    env_vars: Dict[str, str] = {}
    if not env_path.exists():
        return env_vars

    content = env_path.read_text(encoding="utf-8", errors="replace")
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()

        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        else:
            if " #" in val:
                val = val.split(" #", 1)[0].strip()

        env_vars[key] = val
    return env_vars


def load_clouds_yaml(cloud_name: str = "") -> Dict[str, str]:
    """Load credentials from standard OpenStack clouds.yaml if available."""
    candidates = [
        Path.cwd() / "clouds.yaml",
        Path.cwd() / "clouds.yml",
        Path.home() / ".config" / "openstack" / "clouds.yaml",
        Path.home() / ".config" / "openstack" / "clouds.yml",
        Path("/etc/openstack/clouds.yaml"),
    ]
    yaml_path = next((p for p in candidates if p.exists()), None)
    if not yaml_path:
        return {}

    try:
        import yaml  # type: ignore
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        clouds = data.get("clouds", {})
        if not clouds:
            return {}
        target = cloud_name or os.environ.get("OS_CLOUD", "") or next(iter(clouds.keys()))
        cloud_cfg = clouds.get(target, {})
        auth = cloud_cfg.get("auth", {})
        return {
            "OS_AUTH_URL": str(auth.get("auth_url", "")),
            "OS_HORIZON_URL": str(cloud_cfg.get("horizon_url", "")),
            "OS_USERNAME": str(auth.get("username", "")),
            "OS_PASSWORD": str(auth.get("password", "")),
            "OS_PROJECT_ID": str(auth.get("project_id", "")),
            "OS_PROJECT_NAME": str(auth.get("project_name", "")),
            "OS_USER_DOMAIN_NAME": str(auth.get("user_domain_name", "Default")),
            "OS_PROJECT_DOMAIN_ID": str(auth.get("project_domain_id", "default")),
            "OS_REGION_NAME": str(cloud_cfg.get("region_name", "")),
            "OS_INTERFACE": str(cloud_cfg.get("interface", "public")),
            "OS_APPLICATION_CREDENTIAL_ID": str(auth.get("application_credential_id", "")),
            "OS_APPLICATION_CREDENTIAL_SECRET": str(auth.get("application_credential_secret", "")),
        }
    except Exception:
        return {}


def load_config(env_path: Path = DEFAULT_ENV_PATH) -> Dict[str, Any]:
    """Load configuration from .env, environment variables (openrc), or clouds.yaml."""
    file_env: Dict[str, str] = {}
    for candidate in (USER_CONFIG_DIR / ".env", Path.cwd() / ".env", BASE_DIR / ".env", env_path):
        if candidate.exists():
            file_env.update(parse_dotenv(candidate))

    clouds_env = load_clouds_yaml()

    def get_val(key: str, default: str = "") -> str:
        if key in file_env and file_env[key] != "":
            return file_env[key]
        if key in os.environ and os.environ[key] != "":
            return os.environ[key]
        if key in clouds_env and clouds_env[key] != "":
            return clouds_env[key]
        return default

    verify_ssl_raw = get_val("OS_VERIFY_SSL", "true").lower()
    verify_ssl = verify_ssl_raw not in ("false", "0", "no", "off")

    try:
        timeout = int(get_val("OS_TIMEOUT", "30"))
    except ValueError:
        timeout = 30

    horizon_url = get_val("OS_HORIZON_URL", "").rstrip("/")
    auth_url = get_val("OS_AUTH_URL", "").rstrip("/")
    # Auto-resolve Horizon URL from OS_AUTH_URL if OS_HORIZON_URL is not explicitly set
    if not horizon_url and auth_url:
        parsed_base = re.sub(r":5000(/.*)?$", "", auth_url)
        parsed_base = re.sub(r"/v3(/.*)?$", "", parsed_base)
        horizon_url = parsed_base.rstrip("/")

    config = {
        "horizon_url": horizon_url,
        "auth_url": auth_url,
        "identity_api_version": get_val("OS_IDENTITY_API_VERSION", "3"),
        "interface": get_val("OS_INTERFACE", "public"),
        "region_name": get_val("OS_REGION_NAME", "default"),
        "project_id": get_val("OS_PROJECT_ID", ""),
        "project_name": get_val("OS_PROJECT_NAME", ""),
        "project_domain_id": get_val("OS_PROJECT_DOMAIN_ID", "default"),
        "user_domain_name": get_val("OS_USER_DOMAIN_NAME", "Default"),
        "username": get_val("OS_USERNAME", ""),
        "user_id": get_val("OS_USER_ID", ""),
        "password": get_val("OS_PASSWORD", ""),
        "app_cred_id": get_val("OS_APPLICATION_CREDENTIAL_ID", ""),
        "app_cred_secret": get_val("OS_APPLICATION_CREDENTIAL_SECRET", ""),
        "verify_ssl": verify_ssl,
        "timeout": timeout,
        "proxy": get_val("OS_PROXY", ""),
        "env_path": str(env_path),
    }
    return config


def write_audit_log(action: str, target: str, params: Dict[str, Any], status: str, details: Any = None) -> None:
    """Record confirmed mutating actions to audit.log (never logs credentials)."""
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "action": action,
        "target": target,
        "params": params,
        "status": status,
        "details": details,
    }
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


class OpenStackClient:
    """
    Resilient OpenStack Client operating via Horizon REST API & Web Gateway
    over HTTPS port 443, bypassing external firewall restrictions on ports 5000/8774/9696.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.base_url = config["horizon_url"]
        self.session = requests.Session()
        self.session.verify = config["verify_ssl"]
        if config.get("proxy"):
            self.session.proxies = {
                "http": config["proxy"],
                "https": config["proxy"],
            }
        self.authenticated = False
        self.user_session_info: Dict[str, Any] = {}

    def _config_fingerprint(self) -> str:
        raw = f"{self.base_url}|{self.config['username']}|{self.config['project_id']}|{self.config['password']}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_cached_session(self) -> bool:
        if not TOKEN_CACHE_PATH.exists():
            return False
        try:
            data = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            if data.get("fingerprint") != self._config_fingerprint():
                return False
            cookies = data.get("cookies", {})
            if not cookies:
                return False
            requests.utils.add_dict_to_cookiejar(self.session.cookies, cookies)
            # Verify session is still alive via /api/keystone/user-session/
            resp = self.session.get(
                f"{self.base_url}/api/keystone/user-session/",
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                timeout=self.config["timeout"],
            )
            if resp.status_code == 200:
                self.user_session_info = resp.json()
                self.authenticated = True
                return True
            return False
        except Exception:
            return False

    def _save_cached_session(self) -> None:
        try:
            TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "fingerprint": self._config_fingerprint(),
                "cookies": requests.utils.dict_from_cookiejar(self.session.cookies),
                "user_session_info": self.user_session_info,
                "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            TOKEN_CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(TOKEN_CACHE_PATH, 0o600)
        except Exception:
            pass

    def authenticate(self, force_refresh: bool = False) -> Dict[str, Any]:
        if not self.base_url:
            raise OpenStackError(
                "آدرس پنل یا درگاه ابری (OS_HORIZON_URL یا OS_AUTH_URL) تنظیم نشده است. لطفاً دستور `./osctl init` را اجرا کنید یا فایل `.env` را تنظیم نمایید.",
                code="MISSING_CONFIG",
                details={"hint": "Run './osctl init' or create a .env file from .env.example"},
            )
        if not self.config.get("username"):
            raise OpenStackError(
                "نام کاربری (OS_USERNAME) در تنظیمات یافت نشد. لطفاً دستور `./osctl init` را اجرا کنید یا در فایل `.env` مقدار OS_USERNAME را وارد نمایید.",
                code="MISSING_USERNAME",
            )
        has_password = bool(
            self.config.get("password")
            and self.config["password"] not in ("YOUR_PASSWORD_HERE", "YOUR_PANEL_PASSWORD_HERE")
        )
        if not has_password:
            raise OpenStackError(
                f"رمز عبور پنل (OS_PASSWORD) تنظیم نشده است. لطفاً فایل {self.config.get('env_path', '.env')} را باز کرده و رمز ورود خود را مقابل OS_PASSWORD وارد نمایید (یا دستور ./osctl init را اجرا کنید).",
                code="MISSING_PASSWORD",
            )

        if not force_refresh and self._load_cached_session():
            return {
                "authenticated": True,
                "cached": True,
                "gateway": self.base_url,
                "user": {
                    "id": self.user_session_info.get("user_id"),
                    "username": self.user_session_info.get("username"),
                    "domain": self.user_session_info.get("user_domain_name"),
                },
                "project": {
                    "id": self.user_session_info.get("project_id"),
                    "name": self.user_session_info.get("project_name"),
                },
                "region": self.user_session_info.get("services_region"),
            }

        self.session.cookies.clear()
        login_url = f"{self.base_url}/auth/login/"
        try:
            r = self.session.get(login_url, timeout=self.config["timeout"])
        except requests.exceptions.RequestException as e:
            raise OpenStackError(
                f"خطا در اتصال به درگاه ابری ({login_url}): {e}",
                code="NETWORK_ERROR",
            )

        m_csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text)
        csrf_val = m_csrf.group(1) if m_csrf else self.session.cookies.get("csrftoken", "")
        m_reg = re.search(r'name="region" value="([^"]+)"', r.text)
        reg_val = m_reg.group(1) if m_reg else "default"

        login_data = {
            "csrfmiddlewaretoken": csrf_val,
            "region": reg_val,
            "username": self.config["username"],
            "password": self.config["password"],
        }
        if re.search(r'name="domain"', r.text):
            login_data["domain"] = self.config.get("user_domain_name", "Default")

        r2 = self.session.post(
            login_url,
            data=login_data,
            headers={"Referer": login_url},
            timeout=self.config["timeout"],
        )

        # Verify session via /api/keystone/user-session/
        r_sess = self.session.get(
            f"{self.base_url}/api/keystone/user-session/",
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=self.config["timeout"],
        )
        if r_sess.status_code != 200:
            raise OpenStackError(
                "احراز هویت ناموفق بود: نام کاربری یا رمز عبور (OS_PASSWORD) در فایل .env نادرست است.",
                code="AUTH_FAILED",
                status_code=401,
            )

        self.user_session_info = r_sess.json()
        self.authenticated = True
        self._save_cached_session()

        return {
            "authenticated": True,
            "cached": False,
            "gateway": self.base_url,
            "user": {
                "id": self.user_session_info.get("user_id"),
                "username": self.user_session_info.get("username"),
                "domain": self.user_session_info.get("user_domain_name"),
            },
            "project": {
                "id": self.user_session_info.get("project_id"),
                "name": self.user_session_info.get("project_name"),
            },
            "region": self.user_session_info.get("services_region"),
        }

    def api_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if not self.authenticated:
            self.authenticate()

        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = self.session.get(url, params=params, headers=headers, timeout=self.config["timeout"])
        if resp.status_code in (401, 403):
            self.authenticate(force_refresh=True)
            resp = self.session.get(url, params=params, headers=headers, timeout=self.config["timeout"])

        if resp.status_code != 200:
            raise OpenStackError(
                f"خطا در فراخوانی API [{path}] (کد {resp.status_code}): {resp.text[:300]}",
                code="API_ERROR",
                status_code=resp.status_code,
            )
        return resp.json()

    def api_request(
        self,
        method: str,
        path: str,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        expected_status: Tuple[int, ...] = (200, 201, 202, 204),
    ) -> Any:
        if not self.authenticated:
            self.authenticate()

        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        csrf_cookie = self.session.cookies.get("csrftoken", "")
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrf_cookie,
            "Referer": f"{self.base_url}/project/",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        resp = self.session.request(
            method.upper(), url, params=params, json=json_body, headers=headers, timeout=self.config["timeout"]
        )
        if resp.status_code in (401, 403):
            self.authenticate(force_refresh=True)
            headers["X-CSRFToken"] = self.session.cookies.get("csrftoken", "")
            resp = self.session.request(
                method.upper(), url, params=params, json=json_body, headers=headers, timeout=self.config["timeout"]
            )

        if resp.status_code not in expected_status:
            raise OpenStackError(
                f"خطا در فراخوانی [{method.upper()} {path}] (کد {resp.status_code}): {resp.text[:400]}",
                code="API_ERROR",
                status_code=resp.status_code,
            )
        if resp.status_code == 204 or not resp.text.strip():
            return {}
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    # --------------------------------------------------------------------------
    # Compute (Nova) Helpers
    # --------------------------------------------------------------------------
    def list_flavors_map(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = self.api_get("/api/nova/flavors/")
            return {str(f["id"]): f for f in data.get("items", [])}
        except Exception:
            return {}

    def resolve_flavor(self, query: str) -> Dict[str, Any]:
        q = query.strip().lower()
        flavors = self.api_get("/api/nova/flavors/").get("items", [])
        for f in flavors:
            if str(f.get("id", "")).lower() == q or (f.get("name") or "").lower() == q:
                return f
        partial = [f for f in flavors if q in (f.get("name") or "").lower()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise OpenStackError(
                f"بیش از یک تیپ سخت‌افزاری (Flavor) با '{query}' مطابقت دارد.",
                code="AMBIGUOUS_FLAVOR",
                details={
                    "matches": [
                        {"id": f["id"], "name": f["name"], "vcpus": f["vcpus"], "ram_mb": f["ram"]} for f in partial
                    ]
                },
            )
        raise OpenStackError(
            f"تیپ سخت‌افزاری (Flavor) '{query}' یافت نشد.",
            code="FLAVOR_NOT_FOUND",
            details={
                "available": [
                    {"id": f["id"], "name": f["name"], "vcpus": f["vcpus"], "ram_mb": f["ram"]} for f in flavors
                ]
            },
        )

    def list_servers(self, detailed: bool = True) -> List[Dict[str, Any]]:
        data = self.api_get("/api/nova/servers/")
        servers = data.get("items", [])
        flavors_map = self.list_flavors_map() if detailed else {}

        # Also map security groups per server from ports so we always have accurate SGs
        ports_by_device: Dict[str, List[str]] = {}
        if detailed:
            try:
                ports = self.api_get("/api/neutron/ports/").get("items", [])
                sgs = {sg["id"]: sg["name"] for sg in self.list_security_groups()}
                for p in ports:
                    dev_id = p.get("device_id")
                    if dev_id:
                        for sg_id in p.get("security_groups", []):
                            sg_name = sgs.get(sg_id, sg_id)
                            lst = ports_by_device.setdefault(dev_id, [])
                            if sg_name not in lst:
                                lst.append(sg_name)
            except Exception:
                pass

        result = []
        for srv in servers:
            result.append(self._format_server(srv, flavors_map, ports_by_device.get(srv.get("id"))))
        return result

    def _format_server(
        self,
        srv: Dict[str, Any],
        flavors_map: Optional[Dict[str, Any]] = None,
        port_sgs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        addresses = srv.get("addresses", {})
        ips_all: List[str] = []
        fixed_ips: List[str] = []
        floating_ips: List[str] = []
        networks_info: Dict[str, List[Dict[str, str]]] = {}

        for net_name, addr_list in addresses.items():
            networks_info[net_name] = []
            for addr_obj in addr_list:
                ip = addr_obj.get("addr", "")
                ip_type = addr_obj.get("OS-EXT-IPS:type", "fixed")
                mac = addr_obj.get("OS-EXT-IPS-MAC:mac_addr", "")
                if ip:
                    ips_all.append(ip)
                    if ip_type == "floating":
                        floating_ips.append(ip)
                    else:
                        fixed_ips.append(ip)
                networks_info[net_name].append({"ip": ip, "type": ip_type, "mac": mac})

        flavor_obj = srv.get("flavor", {})
        flavor_id = str(flavor_obj.get("id", ""))
        flavor_info = {"id": flavor_id, "name": flavor_obj.get("original_name") or flavor_id}
        if flavors_map and flavor_id in flavors_map:
            fl = flavors_map[flavor_id]
            flavor_info = {
                "id": flavor_id,
                "name": fl.get("name"),
                "vcpus": fl.get("vcpus"),
                "ram_mb": fl.get("ram"),
                "disk_gb": fl.get("disk"),
            }

        sg_names = list(port_sgs) if port_sgs else []
        for sg in srv.get("security_groups", []):
            if isinstance(sg, dict) and sg.get("name") and sg["name"] not in sg_names:
                sg_names.append(sg["name"])

        volumes_attached = [
            v.get("id") for v in srv.get("os-extended-volumes:volumes_attached", []) if isinstance(v, dict) and v.get("id")
        ]

        power_state_map = {
            0: "NOSTATE",
            1: "Running",
            3: "Paused",
            4: "Shutdown",
            6: "Crashed",
            7: "Suspended",
        }
        p_state = srv.get("OS-EXT-STS:power_state")

        return {
            "id": srv.get("id"),
            "name": srv.get("name"),
            "status": srv.get("status"),
            "power_state": power_state_map.get(p_state, str(p_state)),
            "task_state": srv.get("OS-EXT-STS:task_state"),
            "vm_state": srv.get("OS-EXT-STS:vm_state"),
            "ips": ips_all,
            "fixed_ips": fixed_ips,
            "floating_ips": floating_ips,
            "networks": networks_info,
            "security_groups": sg_names,
            "flavor": flavor_info,
            "image_id": srv.get("image", {}).get("id") if isinstance(srv.get("image"), dict) else None,
            "key_name": srv.get("key_name"),
            "volumes_attached": volumes_attached,
            "created": srv.get("created"),
            "updated": srv.get("updated"),
            "availability_zone": srv.get("OS-EXT-AZ:availability_zone"),
        }

    def find_servers(self, query: str) -> List[Dict[str, Any]]:
        """Smart search for servers by exact ID, exact name, exact IP, or fuzzy name/IP."""
        q = query.strip()
        q_lower = q.lower()
        all_servers = self.list_servers(detailed=True)

        exact_id = [s for s in all_servers if s["id"].lower() == q_lower]
        if exact_id:
            return exact_id

        exact_name = [s for s in all_servers if (s["name"] or "").lower() == q_lower]
        if len(exact_name) == 1:
            return exact_name

        exact_ip = [s for s in all_servers if q in s.get("ips", [])]
        if len(exact_ip) == 1:
            return exact_ip

        partial_matches = []
        for s in all_servers:
            name_l = (s["name"] or "").lower()
            ip_match = any(q in ip for ip in s.get("ips", []))
            if q_lower in name_l or ip_match:
                partial_matches.append(s)

        return partial_matches

    def resolve_single_server(self, query: str) -> Dict[str, Any]:
        matches = self.find_servers(query)
        if not matches:
            all_srv = self.list_servers(detailed=True)
            available = [{"id": s["id"], "name": s["name"], "ips": s["ips"], "status": s["status"]} for s in all_srv]
            raise OpenStackError(
                f"هیچ سروری با مشخصات '{query}' پیدا نشد.",
                code="SERVER_NOT_FOUND",
                details={"query": query, "available_servers": available},
            )
        if len(matches) > 1:
            raise OpenStackError(
                f"بیش از یک سرور ({len(matches)} مورد) با عبارت '{query}' مطابقت دارد. لطفاً مشخص کنید کدام سرور مد نظر شماست.",
                code="AMBIGUOUS_SERVER",
                details={
                    "query": query,
                    "matches": [
                        {
                            "id": s["id"],
                            "name": s["name"],
                            "status": s["status"],
                            "ips": s["ips"],
                            "security_groups": s["security_groups"],
                        }
                        for s in matches
                    ],
                },
            )
        return matches[0]

    def execute_horizon_instance_action(self, server_id: str, horizon_action: str) -> None:
        """Execute an instance action (start, stop, reboot, soft_reboot, pause, unpause, suspend, resume, delete) via Horizon."""
        if not self.authenticated:
            self.authenticate()
        inst_url = f"{self.base_url}/project/instances/"
        r_page = self.session.get(inst_url, timeout=self.config["timeout"])
        m_csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r_page.text)
        csrf_val = m_csrf.group(1) if m_csrf else self.session.cookies.get("csrftoken", "")

        action_val = f"instances__{horizon_action}__{server_id}"
        resp = self.session.post(
            inst_url,
            data={"csrfmiddlewaretoken": csrf_val, "action": action_val},
            headers={"Referer": inst_url},
            timeout=self.config["timeout"],
        )
        if resp.status_code not in (200, 201, 202, 204, 302):
            raise OpenStackError(
                f"خطا در اجرای عملیات '{horizon_action}' روی سرور (کد {resp.status_code}).",
                code="INSTANCE_ACTION_FAILED",
                status_code=resp.status_code,
            )

    # --------------------------------------------------------------------------
    # Network & Security Groups (Neutron via Horizon) Helpers
    # --------------------------------------------------------------------------
    def list_security_groups(self) -> List[Dict[str, Any]]:
        data = self.api_get("/api/network/securitygroups/")
        sgs = data.get("items", [])
        return [self._format_sg(sg) for sg in sgs]

    def _format_sg(self, sg: Dict[str, Any]) -> Dict[str, Any]:
        rules = []
        for r in sg.get("security_group_rules", []):
            rules.append(self._format_sg_rule(r))
        return {
            "id": sg.get("id"),
            "name": sg.get("name"),
            "description": sg.get("description") or "",
            "project_id": sg.get("project_id") or sg.get("tenant_id"),
            "rules_count": len(rules),
            "rules": rules,
        }

    def _format_sg_rule(self, r: Dict[str, Any]) -> Dict[str, Any]:
        p_min = r.get("port_range_min")
        p_max = r.get("port_range_max")
        if p_min is None and p_max is None:
            port_display = "ALL"
        elif p_min == p_max:
            port_display = str(p_min)
        else:
            port_display = f"{p_min}:{p_max}"

        return {
            "id": r.get("id"),
            "direction": r.get("direction"),
            "ethertype": r.get("ethertype", "IPv4"),
            "protocol": r.get("protocol") or "ANY",
            "port_range_min": p_min,
            "port_range_max": p_max,
            "port_display": port_display,
            "remote_ip_prefix": r.get("remote_ip_prefix"),
            "remote_group_id": r.get("remote_group_id"),
            "description": r.get("description") or "",
        }

    def get_server_ports_and_sgs(self, server_query: str) -> Dict[str, Any]:
        """Inspect a server and return all attached ports and full Security Group details."""
        srv = self.resolve_single_server(server_query)
        ports_data = self.api_get("/api/neutron/ports/", params={"device_id": srv["id"]})
        ports = ports_data.get("items", [])

        all_sgs = self.list_security_groups()
        sgs_by_id = {sg["id"]: sg for sg in all_sgs}
        sgs_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for sg in all_sgs:
            sgs_by_name.setdefault(sg["name"], []).append(sg)

        attached_sg_ids: List[str] = []
        formatted_ports = []

        fips_data = self.api_get("/api/network/floatingips/")
        fips_by_port: Dict[str, List[str]] = {}
        for fip in fips_data.get("items", []):
            pid = fip.get("port_id")
            f_addr = fip.get("floating_ip_address") or fip.get("ip")
            if pid and f_addr:
                fips_by_port.setdefault(pid, []).append(f_addr)

        for p in ports:
            pid = p.get("id")
            p_sgs = p.get("security_groups", [])
            for sg_id in p_sgs:
                if sg_id not in attached_sg_ids:
                    attached_sg_ids.append(sg_id)
            fixed_ips = [f.get("ip_address") for f in p.get("fixed_ips", []) if f.get("ip_address")]
            formatted_ports.append(
                {
                    "port_id": pid,
                    "name": p.get("name") or "",
                    "mac_address": p.get("mac_address"),
                    "status": p.get("status"),
                    "fixed_ips": fixed_ips,
                    "floating_ips": fips_by_port.get(pid, []),
                    "port_security_enabled": p.get("port_security_enabled", True),
                    "security_group_ids": p_sgs,
                    "security_group_names": [sgs_by_id[sid]["name"] for sid in p_sgs if sid in sgs_by_id],
                }
            )

        if not attached_sg_ids and srv.get("security_groups"):
            for sg_name in srv["security_groups"]:
                for sg_obj in sgs_by_name.get(sg_name, []):
                    if sg_obj["id"] not in attached_sg_ids:
                        attached_sg_ids.append(sg_obj["id"])

        attached_sgs = [sgs_by_id[sid] for sid in attached_sg_ids if sid in sgs_by_id]
        other_sgs = [
            {"id": sg["id"], "name": sg["name"], "description": sg["description"], "rules_count": sg["rules_count"]}
            for sg in all_sgs
            if sg["id"] not in attached_sg_ids
        ]

        return {
            "server": srv,
            "ports": formatted_ports,
            "attached_security_groups_count": len(attached_sgs),
            "attached_security_groups": attached_sgs,
            "other_project_security_groups": other_sgs,
        }

    def resolve_single_sg(self, sg_query: str, server_query: Optional[str] = None) -> Dict[str, Any]:
        """Resolve a security group by ID or name (optionally scoped to a specific server first)."""
        q = sg_query.strip()
        q_lower = q.lower()

        if server_query:
            srv_info = self.get_server_ports_and_sgs(server_query)
            candidates = srv_info["attached_security_groups"]
        else:
            candidates = self.list_security_groups()

        exact_id = [sg for sg in candidates if sg["id"].lower() == q_lower]
        if len(exact_id) == 1:
            return exact_id[0]

        exact_name = [sg for sg in candidates if (sg["name"] or "").lower() == q_lower]
        if len(exact_name) == 1:
            return exact_name[0]
        if len(exact_name) > 1:
            raise OpenStackError(
                f"بیش از یک سکیوریتی گروپ با نام '{q}' وجود دارد. لطفاً از شناسه (ID) سکیوریتی گروپ استفاده کنید.",
                code="AMBIGUOUS_SECURITY_GROUP",
                details={"matches": exact_name},
            )

        partial = [
            sg
            for sg in candidates
            if q_lower in (sg["name"] or "").lower() or q_lower in (sg["description"] or "").lower()
        ]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise OpenStackError(
                f"بیش از یک سکیوریتی گروپ با عبارت '{q}' مطابقت دارد. لطفاً مشخص کنید کدام مورد مد نظر است.",
                code="AMBIGUOUS_SECURITY_GROUP",
                details={"matches": partial},
            )

        if server_query:
            all_sgs = self.list_security_groups()
            exact_all = [sg for sg in all_sgs if sg["id"].lower() == q_lower or (sg["name"] or "").lower() == q_lower]
            if exact_all:
                raise OpenStackError(
                    f"سکیوریتی گروپ '{q}' در پروژه وجود دارد اما به سرور '{server_query}' متصل نیست!",
                    code="SG_NOT_ATTACHED_TO_SERVER",
                    details={"found_sg": exact_all[0]},
                )

        raise OpenStackError(
            f"سکیوریتی گروپ '{q}' یافت نشد.",
            code="SECURITY_GROUP_NOT_FOUND",
            details={
                "available": [{"id": s["id"], "name": s["name"], "description": s["description"]} for s in candidates]
            },
        )

    def horizon_add_sg_rule(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Add a security group rule via Horizon's add_rule view and verify its creation."""
        if not self.authenticated:
            self.authenticate()

        sg_id = spec["security_group_id"]
        add_url = f"{self.base_url}/project/security_groups/{sg_id}/add_rule/"
        r_form = self.session.get(
            add_url,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=self.config["timeout"],
        )
        m_csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r_form.text)
        csrf_val = m_csrf.group(1) if m_csrf else self.session.cookies.get("csrftoken", "")

        proto = spec.get("protocol")
        p_min = spec.get("port_range_min")
        p_max = spec.get("port_range_max")

        if proto == "icmp":
            rule_menu = "all_icmp"
            port_or_range = "all"
            port_val = ""
            from_port = ""
            to_port = ""
        elif proto in ("tcp", "udp"):
            if p_min is None and p_max is None:
                rule_menu = f"all_{proto}"
                port_or_range = "all"
                port_val = ""
                from_port = ""
                to_port = ""
            elif p_min == p_max:
                rule_menu = proto
                port_or_range = "port"
                port_val = str(p_min)
                from_port = ""
                to_port = ""
            else:
                rule_menu = proto
                port_or_range = "range"
                port_val = ""
                from_port = str(p_min)
                to_port = str(p_max)
        else:
            # ANY protocol -> create rules or custom protocol
            rule_menu = "custom"
            port_or_range = "all"
            port_val = ""
            from_port = ""
            to_port = ""

        form_data = {
            "csrfmiddlewaretoken": csrf_val,
            "id": sg_id,
            "rule_menu": rule_menu,
            "direction": spec.get("direction", "ingress"),
            "ip_protocol": "-1" if proto is None else "",
            "port_or_range": port_or_range,
            "port": port_val,
            "from_port": from_port,
            "to_port": to_port,
            "icmp_type": "",
            "icmp_code": "",
            "remote": "cidr",
            "cidr": spec["remote_ip_prefix"],
            "security_group": sg_id,
            "ethertype": spec.get("ethertype", "IPv4"),
            "description": spec.get("description") or "",
        }

        resp = self.session.post(
            add_url,
            data=form_data,
            headers={
                "Referer": f"{self.base_url}/project/security_groups/{sg_id}/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.config["timeout"],
        )
        if resp.status_code not in (200, 201, 202, 204, 302):
            raise OpenStackError(
                f"خطا در ثبت قانون امنیتی در Horizon (کد {resp.status_code}): {resp.text[:400]}",
                code="ADD_RULE_FAILED",
                status_code=resp.status_code,
            )

        # Verify rule in updated security group
        updated_sg = self.resolve_single_sg(sg_id)
        for r in updated_sg.get("rules", []):
            r_proto = None if (r.get("protocol") or "ANY") == "ANY" else r["protocol"].lower()
            if (
                r.get("direction") == spec.get("direction", "ingress")
                and r.get("ethertype") == spec.get("ethertype", "IPv4")
                and r_proto == proto
                and r.get("port_range_min") == p_min
                and r.get("port_range_max") == p_max
                and (r.get("remote_ip_prefix") == spec["remote_ip_prefix"])
            ):
                return r

        return spec

    def horizon_update_server_sgs(self, server_id: str, wanted_sg_ids: List[str]) -> None:
        """Update the list of security group IDs attached to a server via Horizon."""
        if not self.authenticated:
            self.authenticate()
        update_url = f"{self.base_url}/project/instances/{server_id}/update?step=update_security_groups"
        r_form = self.session.get(
            update_url,
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=self.config["timeout"],
        )
        m_csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r_form.text)
        csrf_val = m_csrf.group(1) if m_csrf else self.session.cookies.get("csrftoken", "")
        m_name = re.search(r'name="name" value="([^"]*)"', r_form.text)
        srv_name = m_name.group(1) if m_name else ""

        payload: List[Tuple[str, str]] = [
            ("csrfmiddlewaretoken", csrf_val),
            ("name", srv_name),
            ("description", ""),
            ("default_update_security_groups_role", "member"),
        ]
        for sg_id in wanted_sg_ids:
            payload.append(("update_security_groups_role_member", sg_id))

        resp = self.session.post(
            f"{self.base_url}/project/instances/{server_id}/update",
            data=payload,
            headers={
                "Referer": f"{self.base_url}/project/instances/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.config["timeout"],
        )
        if resp.status_code not in (200, 201, 202, 204, 302):
            raise OpenStackError(
                f"خطا در بروزرسانی سکیوریتی گروپ‌های سرور (کد {resp.status_code}): {resp.text[:300]}",
                code="UPDATE_SERVER_SGS_FAILED",
                status_code=resp.status_code,
            )

    def horizon_delete_sg_rule(self, sg_id: str, rule_id: str) -> None:
        if not self.authenticated:
            self.authenticate()
        sg_url = f"{self.base_url}/project/security_groups/{sg_id}/"
        r_page = self.session.get(sg_url, timeout=self.config["timeout"])
        m_csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r_page.text)
        csrf_val = m_csrf.group(1) if m_csrf else self.session.cookies.get("csrftoken", "")

        action_val = f"rules__delete__{rule_id}"
        resp = self.session.post(
            sg_url,
            data={"csrfmiddlewaretoken": csrf_val, "action": action_val},
            headers={"Referer": sg_url},
            timeout=self.config["timeout"],
        )
        if resp.status_code not in (200, 201, 202, 204, 302):
            raise OpenStackError(
                f"خطا در حذف قانون امنیتی '{rule_id}' (کد {resp.status_code}).",
                code="DELETE_RULE_FAILED",
                status_code=resp.status_code,
            )


def normalize_ip_cidr(ip_input: str) -> Tuple[str, str]:
    """
    Validate and normalize an IP or CIDR string.
    Returns (normalized_cidr, ethertype) e.g. ('1.2.3.4/32', 'IPv4').
    """
    raw = ip_input.strip()
    if raw.lower() in ("any", "all", "0.0.0.0/0", "*"):
        return ("0.0.0.0/0", "IPv4")
    if raw == "::/0":
        return ("::/0", "IPv6")

    try:
        if "/" in raw:
            net = ipaddress.ip_network(raw, strict=False)
        else:
            addr = ipaddress.ip_address(raw)
            prefix = 32 if addr.version == 4 else 128
            net = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
        ethertype = "IPv4" if net.version == 4 else "IPv6"
        return (str(net), ethertype)
    except ValueError:
        raise OpenStackError(
            f"آدرس آی‌پی یا رنج شبکه '{ip_input}' معتبر نیست. نمونه صحیح: 185.10.20.30 یا 192.168.1.0/24",
            code="INVALID_IP_ADDRESS",
        )


def resolve_service_and_ports(
    port_spec: Optional[str], protocol_spec: Optional[str]
) -> Tuple[Optional[str], List[Tuple[Optional[int], Optional[int]]]]:
    """
    Resolve named service aliases (like 'ssh', 'rdp', 'web', 'ping') or numeric port specifications.
    Returns (normalized_protocol_or_None, list_of_(port_min, port_max)).
    """
    p_raw = (port_spec or "all").strip().lower()
    proto_raw = (protocol_spec or "tcp").strip().lower()

    if p_raw in SERVICE_PORT_ALIASES:
        alias_proto, alias_ports = SERVICE_PORT_ALIASES[p_raw]
        if proto_raw == "tcp":
            proto_raw = alias_proto
        p_raw = alias_ports

    if proto_raw == "ping":
        proto_raw = "icmp"
        p_raw = "all"

    if proto_raw in ("any", "all", "*"):
        protocol = None
    elif proto_raw in ("tcp", "udp", "icmp"):
        protocol = proto_raw
    else:
        raise OpenStackError(
            f"پروتکل '{protocol_spec}' نامعتبر است. مقادیر مجاز: tcp, udp, icmp, any",
            code="INVALID_PROTOCOL",
        )

    if protocol in (None, "icmp"):
        if p_raw not in ("all", "any", "*"):
            raise OpenStackError(
                "هنگامی که پروتکل ANY یا ICMP انتخاب شده باشد، نمی‌توان شماره پورت مشخص کرد (برای پورت خاص از پروتکل tcp یا udp استفاده کنید).",
                code="INVALID_PORT_FOR_PROTOCOL",
            )
        return (protocol, [(None, None)])

    if p_raw in ("all", "any", "*"):
        return (protocol, [(None, None)])

    results: List[Tuple[Optional[int], Optional[int]]] = []
    parts = [p.strip() for p in p_raw.split(",") if p.strip()]
    for part in parts:
        if part in SERVICE_PORT_ALIASES:
            _, sub_ports = SERVICE_PORT_ALIASES[part]
            _, sub_ranges = resolve_service_and_ports(sub_ports, protocol)
            results.extend(sub_ranges)
            continue

        sep = ":" if ":" in part else ("-" if "-" in part else None)
        if sep:
            p1_s, p2_s = part.split(sep, 1)
            try:
                p1, p2 = int(p1_s.strip()), int(p2_s.strip())
            except ValueError:
                raise OpenStackError(f"فرمت بازه پورت '{part}' نامعتبر است.", code="INVALID_PORT")
            if not (1 <= p1 <= 65535 and 1 <= p2 <= 65535 and p1 <= p2):
                raise OpenStackError(f"بازه پورت '{part}' باید بین 1 تا 65535 باشد.", code="INVALID_PORT_RANGE")
            results.append((p1, p2))
        else:
            try:
                p = int(part)
            except ValueError:
                raise OpenStackError(f"شماره پورت '{part}' نامعتبر است.", code="INVALID_PORT")
            if not (1 <= p <= 65535):
                raise OpenStackError(f"شماره پورت '{p}' باید بین 1 تا 65535 باشد.", code="INVALID_PORT_RANGE")
            results.append((p, p))

    return (protocol, results)


def check_rule_covers_ip(
    rule: Dict[str, Any], target_cidr: str, direction: str, protocol: Optional[str], port: Optional[int]
) -> bool:
    """Check if an existing security group rule covers the given IP/port/protocol."""
    if rule.get("direction") != direction:
        return False

    remote_prefix = rule.get("remote_ip_prefix")
    if not remote_prefix:
        return False

    try:
        rule_net = ipaddress.ip_network(remote_prefix, strict=False)
        target_net = ipaddress.ip_network(target_cidr, strict=False)
        if rule_net.version != target_net.version:
            return False
        if not target_net.subnet_of(rule_net):
            return False
    except Exception:
        return False

    rule_proto = (rule.get("protocol") or "ANY").lower()
    if rule_proto != "any" and protocol and rule_proto != protocol.lower():
        return False

    if port is not None:
        p_min = rule.get("port_range_min")
        p_max = rule.get("port_range_max")
        if p_min is not None and p_max is not None:
            if not (p_min <= port <= p_max):
                return False

    return True


# ==============================================================================
# Command Handlers
# ==============================================================================

def cmd_auth_check(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    return client.authenticate(force_refresh=args.refresh)


def cmd_overview(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    auth = client.authenticate()
    servers = client.list_servers(detailed=True)
    sgs = client.list_security_groups()
    fips = client.api_get("/api/network/floatingips/").get("items", [])
    volumes = []
    try:
        volumes = client.api_get("/api/cinder/volumes/").get("items", [])
    except Exception:
        pass

    srv_by_status: Dict[str, int] = {}
    for s in servers:
        st = s.get("status", "UNKNOWN")
        srv_by_status[st] = srv_by_status.get(st, 0) + 1

    return {
        "project": auth.get("project"),
        "user": auth.get("user"),
        "summary": {
            "total_servers": len(servers),
            "servers_by_status": srv_by_status,
            "total_security_groups": len(sgs),
            "total_floating_ips": len(fips),
            "total_volumes": len(volumes),
        },
        "servers": [
            {
                "id": s["id"],
                "name": s["name"],
                "status": s["status"],
                "power_state": s["power_state"],
                "ips": s["ips"],
                "security_groups": s["security_groups"],
                "flavor": s["flavor"].get("name"),
            }
            for s in servers
        ],
    }


# --- Servers Commands ---

def cmd_servers_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    servers = client.list_servers(detailed=True)
    return {"count": len(servers), "servers": servers}


def cmd_servers_find(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    matches = client.find_servers(args.query)
    return {"query": args.query, "count": len(matches), "matches": matches}


def cmd_servers_show(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    return client.get_server_ports_and_sgs(args.server)


def cmd_servers_action(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    srv = client.resolve_single_server(args.server)
    action = args.action_type

    horizon_action_map = {
        "start": "start",
        "stop": "stop",
        "reboot-soft": "soft_reboot",
        "reboot-hard": "reboot",
        "pause": "pause",
        "unpause": "pause",
        "suspend": "suspend",
        "resume": "suspend",
        "resize-confirm": "confirm",
        "resize-revert": "revert",
    }

    action_labels_fa = {
        "start": "روشن کردن سرور (Start)",
        "stop": "خاموش کردن سرور (Shutoff / Stop)",
        "reboot-soft": "راه‌اندازی مجدد نرم سرور (Soft Reboot)",
        "reboot-hard": "راه‌اندازی مجدد سخت سرور (Hard Reboot)",
        "pause": "متوقف کردن موقت سرور (Pause)",
        "unpause": "خروج سرور از حالت توقف (Unpause)",
        "suspend": "تعلیق سرور (Suspend)",
        "resume": "بازگردانی سرور از تعلیق (Resume)",
        "resize-confirm": "تایید نهایی تغییر منابع سرور (Confirm Resize)",
        "resize-revert": "بازگردانی تغییر منابع سرور (Revert Resize)",
    }

    plan = {
        "action": action,
        "action_description": action_labels_fa.get(action, action),
        "server": {
            "id": srv["id"],
            "name": srv["name"],
            "current_status": srv["status"],
            "current_power_state": srv["power_state"],
            "ips": srv["ips"],
        },
    }

    if not args.confirm:
        return {
            "mode": "PREVIEW_ONLY",
            "requires_confirmation": True,
            "message": f"پیش‌نمایش عملیات '{action_labels_fa.get(action)}'. برای اجرای نهایی باید پس از تایید کاربر با فلگ --confirm فراخوانی شود.",
            "plan": plan,
        }

    client.execute_horizon_instance_action(srv["id"], horizon_action_map[action])
    write_audit_log(f"server.{action}", f"{srv['name']} ({srv['id']})", plan, "SUCCESS")
    return {
        "mode": "EXECUTED",
        "success": True,
        "message": f"عملیات '{action_labels_fa.get(action)}' روی سرور '{srv['name']}' ({srv['id']}) با موفقیت ارسال شد.",
        "details": plan,
    }


def cmd_servers_sg_membership(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    srv_info = client.get_server_ports_and_sgs(args.server)
    srv = srv_info["server"]
    sg = client.resolve_single_sg(args.sg)
    op = args.op  # attach or detach

    current_sg_ids = [s["id"] for s in srv_info["attached_security_groups"]]
    current_sg_names = [s["name"] for s in srv_info["attached_security_groups"]]

    if op == "attach":
        if sg["id"] in current_sg_ids:
            return {
                "mode": "ALREADY_EXISTS",
                "success": True,
                "message": f"سکیوریتی گروپ '{sg['name']}' از قبل به سرور '{srv['name']}' متصل است.",
            }
        wanted_ids = current_sg_ids + [sg["id"]]
    else:
        if sg["id"] not in current_sg_ids:
            return {
                "mode": "ALREADY_REMOVED",
                "success": True,
                "message": f"سکیوریتی گروپ '{sg['name']}' به سرور '{srv['name']}' متصل نیست.",
            }
        wanted_ids = [sid for sid in current_sg_ids if sid != sg["id"]]

    plan = {
        "action": f"server_{op}_security_group",
        "server": {"id": srv["id"], "name": srv["name"], "ips": srv["ips"], "current_sgs": current_sg_names},
        "target_security_group": {"id": sg["id"], "name": sg["name"]},
        "resulting_sg_ids": wanted_ids,
    }
    if not args.confirm:
        return {
            "mode": "PREVIEW_ONLY",
            "requires_confirmation": True,
            "message": f"پیش‌نمایش {'اتصال' if op == 'attach' else 'جداسازی'} سکیوریتی گروپ '{sg['name']}' روی سرور '{srv['name']}'.",
            "plan": plan,
        }

    client.horizon_update_server_sgs(srv["id"], wanted_ids)
    write_audit_log(f"server.{op}_sg", f"{srv['name']} ({srv['id']})", plan, "SUCCESS")
    return {
        "mode": "EXECUTED",
        "success": True,
        "message": f"سکیوریتی گروپ '{sg['name']}' با موفقیت {'به سرور متصل شد' if op == 'attach' else 'از سرور جدا شد'}.",
        "details": plan,
    }


def cmd_servers_delete(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    srv = client.resolve_single_server(args.server)
    plan = {
        "action": "DELETE_SERVER_PERMANENTLY",
        "warning": "این عملیات غیرقابل بازگشت است و ماشین مجازی به طور کامل حذف خواهد شد!",
        "server": {"id": srv["id"], "name": srv["name"], "status": srv["status"], "ips": srv["ips"]},
    }
    if not args.confirm:
        return {
            "mode": "PREVIEW_ONLY",
            "requires_confirmation": True,
            "message": "پیش‌نمایش حذف دائمی سرور. برای اجرا نیاز به تایید صریح کاربر و فلگ --confirm است.",
            "plan": plan,
        }
    client.execute_horizon_instance_action(srv["id"], "delete")
    write_audit_log("server.delete", f"{srv['name']} ({srv['id']})", plan, "SUCCESS")
    return {
        "mode": "EXECUTED",
        "success": True,
        "message": f"سرور '{srv['name']}' ({srv['id']}) با موفقیت حذف شد.",
        "details": plan,
    }


# --- Security Groups & Firewall Commands ---

def cmd_sg_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    sgs = client.list_security_groups()
    ports = client.api_get("/api/neutron/ports/").get("items", [])
    servers = {s["id"]: s for s in client.list_servers(detailed=False)}

    sg_servers: Dict[str, List[Dict[str, str]]] = {}
    for p in ports:
        dev_id = p.get("device_id")
        if dev_id and dev_id in servers:
            srv_entry = {"id": dev_id, "name": servers[dev_id]["name"]}
            for sg_id in p.get("security_groups", []):
                lst = sg_servers.setdefault(sg_id, [])
                if srv_entry not in lst:
                    lst.append(srv_entry)

    for sg in sgs:
        sg["attached_servers"] = sg_servers.get(sg["id"], [])
        if not args.with_rules:
            del sg["rules"]

    return {"count": len(sgs), "security_groups": sgs}


def cmd_sg_for_server(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    return client.get_server_ports_and_sgs(args.server)


def cmd_sg_show(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    sg = client.resolve_single_sg(args.sg, server_query=args.server)
    # Also find which servers use this SG
    ports = client.api_get("/api/neutron/ports/").get("items", [])
    servers = {s["id"]: s for s in client.list_servers(detailed=False)}
    attached_servers = []
    for p in ports:
        dev_id = p.get("device_id")
        if dev_id and dev_id in servers and sg["id"] in p.get("security_groups", []):
            srv_entry = {"id": dev_id, "name": servers[dev_id]["name"], "ips": servers[dev_id]["ips"]}
            if srv_entry not in attached_servers:
                attached_servers.append(srv_entry)
    sg["attached_servers"] = attached_servers
    return {"security_group": sg}


def cmd_sg_check_access(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    srv_info = client.get_server_ports_and_sgs(args.server)
    cidr, ethertype = normalize_ip_cidr(args.ip)
    protocol, port_ranges = resolve_service_and_ports(args.port, args.protocol)
    direction = (args.direction or "ingress").lower()
    first_port = port_ranges[0][0] if port_ranges else None

    matching_rules = []
    for sg in srv_info["attached_security_groups"]:
        for rule in sg.get("rules", []):
            if check_rule_covers_ip(rule, cidr, direction, protocol, first_port):
                matching_rules.append(
                    {
                        "security_group_id": sg["id"],
                        "security_group_name": sg["name"],
                        "rule": rule,
                    }
                )

    return {
        "server": {
            "id": srv_info["server"]["id"],
            "name": srv_info["server"]["name"],
            "ips": srv_info["server"]["ips"],
        },
        "checked_criteria": {
            "ip_cidr": cidr,
            "ethertype": ethertype,
            "direction": direction,
            "protocol": protocol or "ANY",
            "port": first_port or "ANY",
        },
        "has_access": len(matching_rules) > 0,
        "matching_rules_count": len(matching_rules),
        "matching_rules": matching_rules,
        "attached_security_groups": [
            {"id": sg["id"], "name": sg["name"], "description": sg["description"], "rules_count": sg["rules_count"]}
            for sg in srv_info["attached_security_groups"]
        ],
    }


def cmd_sg_allow(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    srv_summary = None
    if args.server:
        srv_obj = client.resolve_single_server(args.server)
        srv_summary = {"id": srv_obj["id"], "name": srv_obj["name"], "ips": srv_obj["ips"]}

    sg = client.resolve_single_sg(args.sg, server_query=args.server)
    cidr, ethertype = normalize_ip_cidr(args.ip)
    protocol, port_ranges = resolve_service_and_ports(args.port, args.protocol)

    direction = (args.direction or "ingress").strip().lower()
    if direction not in ("ingress", "egress"):
        raise OpenStackError("مقدار direction باید ingress (ورودی) یا egress (خروجی) باشد.", code="INVALID_DIRECTION")

    description = args.description or ""

    rules_to_create = []
    already_existing = []
    covered_by_broader_rules = []

    for p_min, p_max in port_ranges:
        dup = None
        broader = None
        for r in sg.get("rules", []):
            r_proto = None if (r.get("protocol") or "ANY") == "ANY" else r["protocol"].lower()
            if (
                r.get("direction") == direction
                and r.get("ethertype") == ethertype
                and r_proto == protocol
                and r.get("port_range_min") == p_min
                and r.get("port_range_max") == p_max
                and r.get("remote_ip_prefix") == cidr
            ):
                dup = r
                break
            elif check_rule_covers_ip(r, cidr, direction, protocol, p_min):
                broader = r

        rule_spec = {
            "security_group_id": sg["id"],
            "direction": direction,
            "ethertype": ethertype,
            "protocol": protocol,
            "port_range_min": p_min,
            "port_range_max": p_max,
            "remote_ip_prefix": cidr,
            "description": description,
        }
        if dup:
            already_existing.append(dup)
        else:
            rules_to_create.append(rule_spec)
            if broader and broader not in covered_by_broader_rules:
                covered_by_broader_rules.append(broader)

    plan = {
        "action": "allow_ip_access",
        "server": srv_summary,
        "security_group": {"id": sg["id"], "name": sg["name"], "description": sg["description"]},
        "rules_to_create": rules_to_create,
        "already_existing_rules": already_existing,
        "covered_by_existing_broader_rules": covered_by_broader_rules,
    }

    if not rules_to_create and already_existing:
        return {
            "mode": "ALREADY_EXISTS",
            "success": True,
            "message": f"این دسترسی از قبل به صورت دقیق در سکیوریتی گروپ '{sg['name']}' وجود دارد و نیازی به ثبت مجدد نیست.",
            "details": plan,
        }

    if not args.confirm:
        return {
            "mode": "PREVIEW_ONLY",
            "requires_confirmation": True,
            "message": "پیش‌نمایش افزودن قانون دسترسی (Firewall Rule). برای اعمال نهایی نیاز به تایید کاربر و فلگ --confirm است.",
            "plan": plan,
        }

    created_rules = []
    for spec in rules_to_create:
        created_rule = client.horizon_add_sg_rule(spec)
        created_rules.append(created_rule)

    write_audit_log(
        "sg.allow",
        f"{sg['name']} ({sg['id']})",
        {"server": srv_summary, "created_rules": created_rules},
        "SUCCESS",
    )

    return {
        "mode": "EXECUTED",
        "success": True,
        "message": f"{len(created_rules)} قانون دسترسی جدید با موفقیت در سکیوریتی گروپ '{sg['name']}' ثبت شد.",
        "security_group": {"id": sg["id"], "name": sg["name"]},
        "server": srv_summary,
        "created_rules": created_rules,
        "already_existing_rules": already_existing,
    }


def cmd_sg_revoke(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    if args.rule_id:
        all_sgs = client.list_security_groups()
        target_sg = None
        target_rule = None
        for sg in all_sgs:
            for r in sg.get("rules", []):
                if r["id"] == args.rule_id:
                    target_sg = {"id": sg["id"], "name": sg["name"]}
                    target_rule = r
                    break
        if not target_rule or not target_sg:
            raise OpenStackError(f"قانون امنیتی با شناسه '{args.rule_id}' یافت نشد.", code="RULE_NOT_FOUND")

        plan = {
            "action": "revoke_security_group_rule",
            "security_group": target_sg,
            "rules_to_delete": [target_rule],
        }
        if not args.confirm:
            return {
                "mode": "PREVIEW_ONLY",
                "requires_confirmation": True,
                "message": "پیش‌نمایش حذف قانون دسترسی. برای حذف نهایی نیاز به تایید کاربر و فلگ --confirm است.",
                "plan": plan,
            }

        client.horizon_delete_sg_rule(target_sg["id"], args.rule_id)
        write_audit_log("sg.revoke", f"rule:{args.rule_id}", plan, "SUCCESS")
        return {
            "mode": "EXECUTED",
            "success": True,
            "message": f"قانون دسترسی '{args.rule_id}' با موفقیت از سکیوریتی گروپ '{target_sg['name']}' حذف شد.",
            "deleted_rules": [target_rule],
        }

    if not args.sg or not args.ip:
        raise OpenStackError(
            "برای حذف دسترسی یا باید --rule-id بدهید یا ترکیب --sg و --ip را مشخص نمایید.",
            code="MISSING_REVOKE_CRITERIA",
        )

    sg = client.resolve_single_sg(args.sg, server_query=args.server)
    cidr, _ = normalize_ip_cidr(args.ip)
    matched_rules = []
    for r in sg.get("rules", []):
        if r.get("remote_ip_prefix") == cidr:
            if args.protocol and (r.get("protocol") or "ANY").lower() != args.protocol.lower():
                continue
            if args.port and r.get("port_display") != str(args.port):
                continue
            matched_rules.append(r)

    if not matched_rules:
        raise OpenStackError(
            f"هیچ قانونی با آی‌پی '{cidr}' در سکیوریتی گروپ '{sg['name']}' یافت نشد.",
            code="NO_MATCHING_RULE",
            details={"security_group": sg["name"], "existing_rules": sg["rules"]},
        )

    plan = {
        "action": "revoke_security_group_rules_by_match",
        "security_group": {"id": sg["id"], "name": sg["name"]},
        "rules_to_delete": matched_rules,
    }
    if not args.confirm:
        return {
            "mode": "PREVIEW_ONLY",
            "requires_confirmation": True,
            "message": f"{len(matched_rules)} قانون منطبق در سکیوریتی گروپ '{sg['name']}' پیدا شد. برای حذف نهایی نیاز به تایید کاربر و فلگ --confirm است.",
            "plan": plan,
        }

    for r in matched_rules:
        client.horizon_delete_sg_rule(sg["id"], r["id"])

    write_audit_log("sg.revoke_match", f"{sg['name']} ({sg['id']})", plan, "SUCCESS")
    return {
        "mode": "EXECUTED",
        "success": True,
        "message": f"{len(matched_rules)} قانون دسترسی مربوط به '{cidr}' از سکیوریتی گروپ '{sg['name']}' حذف شد.",
        "deleted_rules": matched_rules,
    }


def cmd_sg_audit(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    """AI Security Auditor: Scan all Security Groups for risky 0.0.0.0/0 exposures, duplicates, and unused SGs."""
    sgs = client.list_security_groups()
    ports = client.api_get("/api/neutron/ports/").get("items", [])
    servers = {s["id"]: s for s in client.list_servers(detailed=False)}

    sg_servers: Dict[str, List[Dict[str, Any]]] = {}
    for p in ports:
        dev_id = p.get("device_id")
        if dev_id and dev_id in servers:
            srv_entry = {"id": dev_id, "name": servers[dev_id]["name"], "ips": servers[dev_id]["ips"]}
            for sg_id in p.get("security_groups", []):
                lst = sg_servers.setdefault(sg_id, [])
                if srv_entry not in lst:
                    lst.append(srv_entry)

    sensitive_ports = {
        22: ("SSH", "CRITICAL"),
        3389: ("RDP", "CRITICAL"),
        3306: ("MySQL/MariaDB", "HIGH"),
        5432: ("PostgreSQL", "HIGH"),
        1433: ("MSSQL", "HIGH"),
        6379: ("Redis", "CRITICAL"),
        27017: ("MongoDB", "CRITICAL"),
        9200: ("Elasticsearch", "HIGH"),
        5985: ("WinRM", "HIGH"),
        5986: ("WinRM-HTTPS", "HIGH"),
        2375: ("Docker Daemon", "CRITICAL"),
    }

    findings: List[Dict[str, Any]] = []
    unused_sgs: List[Dict[str, Any]] = []
    duplicate_rules: List[Dict[str, Any]] = []

    for sg in sgs:
        attached = sg_servers.get(sg["id"], [])
        if not attached and sg["name"] != "default":
            unused_sgs.append(
                {
                    "id": sg["id"],
                    "name": sg["name"],
                    "description": sg["description"],
                    "rules_count": sg["rules_count"],
                }
            )

        seen_sigs: Dict[Tuple[Any, ...], str] = {}
        for r in sg.get("rules", []):
            sig = _rule_signature(r)
            if sig in seen_sigs:
                duplicate_rules.append(
                    {
                        "security_group_id": sg["id"],
                        "security_group_name": sg["name"],
                        "duplicate_rule_id": r["id"],
                        "original_rule_id": seen_sigs[sig],
                        "rule": r,
                    }
                )
            else:
                seen_sigs[sig] = r["id"]

            if r.get("direction") != "ingress":
                continue
            cidr = r.get("remote_ip_prefix") or ""
            if cidr in ("0.0.0.0/0", "::/0"):
                proto = (r.get("protocol") or "ANY").upper()
                p_min = r.get("port_range_min")
                p_max = r.get("port_range_max")

                if proto == "ANY" or (proto in ("TCP", "UDP") and p_min is None and p_max is None):
                    findings.append(
                        {
                            "severity": "CRITICAL",
                            "issue": f"All {proto} ports open to the entire Internet ({cidr})",
                            "security_group": {"id": sg["id"], "name": sg["name"]},
                            "rule": r,
                            "affected_servers": attached,
                            "recommendation": f"Restrict rule {r['id']} to trusted IPs or revoke via: ./osctl sg revoke --rule-id {r['id']}",
                        }
                    )
                elif p_min is not None and p_max is not None:
                    for sp, (svc_name, sev) in sensitive_ports.items():
                        if p_min <= sp <= p_max:
                            findings.append(
                                {
                                    "severity": sev,
                                    "issue": f"Sensitive service {svc_name} (port {sp}) open to the entire Internet ({cidr})",
                                    "security_group": {"id": sg["id"], "name": sg["name"]},
                                    "rule": r,
                                    "affected_servers": attached,
                                    "recommendation": f"Limit {svc_name} access to specific admin IPs instead of {cidr}.",
                                }
                            )

    return {
        "summary": {
            "total_security_groups": len(sgs),
            "risk_findings_count": len(findings),
            "critical_count": sum(1 for f in findings if f["severity"] == "CRITICAL"),
            "high_count": sum(1 for f in findings if f["severity"] == "HIGH"),
            "duplicate_rules_count": len(duplicate_rules),
            "unused_security_groups_count": len(unused_sgs),
        },
        "risk_findings": findings,
        "duplicate_rules": duplicate_rules,
        "unused_security_groups": unused_sgs,
    }


def cmd_servers_diagnose(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    """AI Server Diagnostics: Inspect server health, power/task state, network ports, ingress firewall rules, and volumes."""
    info = client.get_server_ports_and_sgs(args.server)
    srv = info["server"]

    ingress_summary = []
    has_ssh = False
    has_rdp = False
    has_icmp = False
    for sg in info["attached_security_groups"]:
        for r in sg.get("rules", []):
            if r.get("direction") == "ingress":
                proto = (r.get("protocol") or "ANY").lower()
                p_min = r.get("port_range_min")
                p_max = r.get("port_range_max")
                if proto in ("any", "icmp"):
                    has_icmp = True
                if proto in ("any", "tcp"):
                    if p_min is None or (p_min <= 22 <= (p_max or 22)):
                        has_ssh = True
                    if p_min is None or (p_min <= 3389 <= (p_max or 3389)):
                        has_rdp = True
                ingress_summary.append(
                    {
                        "sg": sg["name"],
                        "protocol": r.get("protocol") or "ANY",
                        "port": r.get("port_display"),
                        "cidr": r.get("remote_ip_prefix") or r.get("remote_group_id"),
                        "description": r.get("description"),
                    }
                )

    warnings = []
    if srv.get("status") != "ACTIVE":
        warnings.append(f"وضعیت سرور ACTIVE نیست (وضعیت فعلی: {srv.get('status')} / {srv.get('power_state')}).")
    if srv.get("task_state"):
        warnings.append(f"سرور در حال انجام عملیات است (task_state: {srv.get('task_state')}).")
    if not info["ports"]:
        warnings.append("هیچ پورت شبکه‌ای به این سرور متصل نیست!")
    if not info["attached_security_groups"]:
        warnings.append("هیچ سکیوریتی گروپی به پورت‌های این سرور متصل نیست!")
    if not has_ssh and not has_rdp:
        warnings.append("هیچ قانون ورودی برای SSH (22) یا RDP (3389) در سکیوریتی گروپ‌های این سرور باز نیست.")

    return {
        "server": srv,
        "health_check": {
            "is_active": srv.get("status") == "ACTIVE" and srv.get("power_state") == "Running",
            "has_network_ports": len(info["ports"]) > 0,
            "has_floating_ip": len(srv.get("floating_ips", [])) > 0,
            "ssh_allowed_somewhere": has_ssh,
            "rdp_allowed_somewhere": has_rdp,
            "ping_allowed_somewhere": has_icmp,
            "warnings": warnings,
        },
        "ports": info["ports"],
        "attached_security_groups": [
            {"id": sg["id"], "name": sg["name"], "description": sg["description"], "rules_count": sg["rules_count"]}
            for sg in info["attached_security_groups"]
        ],
        "active_ingress_rules": ingress_summary,
    }


# --- Network & Floating IPs Commands ---

def cmd_network_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    nets = client.api_get("/api/neutron/networks/").get("items", [])
    return {"count": len(nets), "networks": nets}


def cmd_fip_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    fips = client.api_get("/api/network/floatingips/").get("items", [])
    ports = {p["id"]: p for p in client.api_get("/api/neutron/ports/").get("items", [])}
    servers = {s["id"]: s for s in client.list_servers(detailed=False)}

    result = []
    for f in fips:
        pid = f.get("port_id")
        srv_info = None
        if pid and pid in ports:
            dev_id = ports[pid].get("device_id")
            if dev_id and dev_id in servers:
                srv_info = {"id": dev_id, "name": servers[dev_id]["name"]}
        result.append(
            {
                "id": f.get("id"),
                "floating_ip": f.get("floating_ip_address") or f.get("ip"),
                "fixed_ip": f.get("fixed_ip_address"),
                "status": f.get("status"),
                "port_id": pid,
                "attached_server": srv_info,
                "description": f.get("description") or "",
            }
        )
    return {"count": len(result), "floating_ips": result}


# --- Volumes & Snapshots (Cinder) Commands ---

def cmd_volumes_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    vols = client.api_get("/api/cinder/volumes/").get("items", [])
    servers = {s["id"]: s["name"] for s in client.list_servers(detailed=False)}

    result = []
    for v in vols:
        attachments = []
        for att in v.get("attachments", []):
            sid = att.get("server_id")
            attachments.append(
                {
                    "server_id": sid,
                    "server_name": servers.get(sid, sid),
                    "device": att.get("device"),
                }
            )
        result.append(
            {
                "id": v.get("id"),
                "name": v.get("name") or "",
                "status": v.get("status"),
                "size_gb": v.get("size"),
                "volume_type": v.get("volume_type"),
                "bootable": str(v.get("bootable")).lower() == "true",
                "attachments": attachments,
                "created_at": v.get("created_at"),
            }
        )
    return {"count": len(result), "volumes": result}


# --- Flavors, Images, Quotas, Audit ---

def cmd_flavors_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    flavors = client.api_get("/api/nova/flavors/").get("items", [])
    return {
        "count": len(flavors),
        "flavors": [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "vcpus": f.get("vcpus"),
                "ram_mb": f.get("ram"),
                "disk_gb": f.get("disk"),
            }
            for f in flavors
        ],
    }


def cmd_images_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    images = client.api_get("/api/glance/images/").get("items", [])
    return {
        "count": len(images),
        "images": [
            {
                "id": img.get("id"),
                "name": img.get("name"),
                "status": img.get("status"),
                "visibility": img.get("visibility"),
                "size_mb": round((img.get("size") or 0) / (1024 * 1024), 1),
                "created_at": img.get("created_at"),
            }
            for img in images
        ],
    }


def cmd_quotas_show(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    limits = client.api_get("/api/nova/limits/")
    return {"limits": limits}


def cmd_backup_create(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    sgs = client.list_security_groups()
    servers = client.list_servers(detailed=True)
    ports = client.api_get("/api/neutron/ports/").get("items", [])

    srv_sg_map: Dict[str, List[str]] = {}
    for p in ports:
        dev_id = p.get("device_id")
        if dev_id:
            lst = srv_sg_map.setdefault(dev_id, [])
            for sid in p.get("security_groups", []):
                if sid not in lst:
                    lst.append(sid)

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = re.sub(r"[^a-zA-Z0-9_-]", "_", args.label or "manual")
    filename = f"backup_{now_str}_{label}.json"
    filepath = BACKUPS_DIR / filename
    latest_path = BACKUPS_DIR / "latest.json"

    snapshot_data = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "label": args.label or "manual",
        "file": str(filepath),
        "security_groups": sgs,
        "server_security_groups": {
            s["id"]: {"name": s["name"], "sg_ids": srv_sg_map.get(s["id"], [])} for s in servers
        },
    }

    content = json.dumps(snapshot_data, ensure_ascii=False, indent=2)
    filepath.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")
    write_audit_log("backup.create", filename, {"label": label}, "SUCCESS")

    return {
        "success": True,
        "message": f"نسخه پشتیبان کامل از وضعیت سکیوریتی گروپ‌ها و سرورها در '{filepath.name}' و 'backups/latest.json' ذخیره شد.",
        "backup_file": str(filepath),
        "security_groups_count": len(sgs),
        "servers_count": len(servers),
    }


def cmd_backups_list(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    if not BACKUPS_DIR.exists():
        return {"count": 0, "backups": []}
    files = sorted(BACKUPS_DIR.glob("backup_*.json"), reverse=True)
    items = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append(
                {
                    "filename": f.name,
                    "path": str(f),
                    "timestamp": data.get("timestamp"),
                    "label": data.get("label"),
                    "security_groups_count": len(data.get("security_groups", [])),
                }
            )
        except Exception:
            pass
    return {"count": len(items), "backups": items}


def _rule_signature(r: Dict[str, Any]) -> Tuple[Any, ...]:
    proto = None if (r.get("protocol") or "ANY").upper() == "ANY" else r.get("protocol", "").lower()
    cidr = r.get("remote_ip_prefix")
    if cidr:
        try:
            cidr = str(ipaddress.ip_network(cidr, strict=False))
        except Exception:
            pass
    return (
        r.get("direction"),
        r.get("ethertype", "IPv4"),
        proto,
        r.get("port_range_min"),
        r.get("port_range_max"),
        cidr,
        r.get("remote_group_id"),
    )


def cmd_revert(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    target_path = Path(args.file) if args.file else (BACKUPS_DIR / "latest.json")
    if not target_path.exists() and not target_path.is_absolute():
        target_path = BACKUPS_DIR / args.file
    if not target_path.exists():
        raise OpenStackError(f"فایل پشتیبان '{target_path}' یافت نشد.", code="BACKUP_NOT_FOUND")

    snap = json.loads(target_path.read_text(encoding="utf-8"))
    snap_sgs = {sg["id"]: sg for sg in snap.get("security_groups", [])}
    current_sgs = {sg["id"]: sg for sg in client.list_security_groups()}

    rules_to_restore: List[Dict[str, Any]] = []
    rules_to_delete: List[Dict[str, Any]] = []

    for sg_id, snap_sg in snap_sgs.items():
        if sg_id not in current_sgs:
            continue
        cur_sg = current_sgs[sg_id]
        snap_rules_by_sig = {_rule_signature(r): r for r in snap_sg.get("rules", [])}
        cur_rules_by_sig = {_rule_signature(r): r for r in cur_sg.get("rules", [])}

        # Rules that were in snapshot but missing now -> re-create
        for sig, r in snap_rules_by_sig.items():
            if sig not in cur_rules_by_sig:
                rules_to_restore.append(
                    {
                        "security_group_id": sg_id,
                        "security_group_name": snap_sg["name"],
                        "direction": r["direction"],
                        "ethertype": r["ethertype"],
                        "protocol": None if (r.get("protocol") or "ANY") == "ANY" else r["protocol"].lower(),
                        "port_range_min": r["port_range_min"],
                        "port_range_max": r["port_range_max"],
                        "remote_ip_prefix": r["remote_ip_prefix"],
                        "description": r.get("description") or "",
                    }
                )

        # Rules that exist now but were NOT in snapshot -> delete
        for sig, r in cur_rules_by_sig.items():
            if sig not in snap_rules_by_sig:
                rules_to_delete.append(
                    {
                        "security_group_id": sg_id,
                        "security_group_name": cur_sg["name"],
                        "rule_id": r["id"],
                        "rule": r,
                    }
                )

    # Check server security group attachments
    ports = client.api_get("/api/neutron/ports/").get("items", [])
    cur_srv_sgs: Dict[str, List[str]] = {}
    for p in ports:
        dev_id = p.get("device_id")
        if dev_id:
            lst = cur_srv_sgs.setdefault(dev_id, [])
            for sid in p.get("security_groups", []):
                if sid not in lst:
                    lst.append(sid)

    servers_to_restore: List[Dict[str, Any]] = []
    for srv_id, snap_srv_info in snap.get("server_security_groups", {}).items():
        snap_ids = sorted(snap_srv_info.get("sg_ids", []))
        cur_ids = sorted(cur_srv_sgs.get(srv_id, []))
        if snap_ids and cur_ids and snap_ids != cur_ids:
            servers_to_restore.append(
                {
                    "server_id": srv_id,
                    "server_name": snap_srv_info.get("name"),
                    "current_sg_ids": cur_ids,
                    "restore_sg_ids": snap_ids,
                }
            )

    plan = {
        "backup_file": str(target_path),
        "backup_timestamp": snap.get("timestamp"),
        "backup_label": snap.get("label"),
        "rules_to_restore_count": len(rules_to_restore),
        "rules_to_restore": rules_to_restore,
        "rules_to_delete_count": len(rules_to_delete),
        "rules_to_delete": rules_to_delete,
        "servers_sg_to_restore_count": len(servers_to_restore),
        "servers_sg_to_restore": servers_to_restore,
    }

    if not rules_to_restore and not rules_to_delete and not servers_to_restore:
        return {
            "mode": "NO_CHANGES_NEEDED",
            "success": True,
            "message": "وضعیت فعلی دقیقاً منطبق بر نسخه پشتیبان است و هیچ تغییری برای بازگردانی وجود ندارد.",
            "plan": plan,
        }

    if not args.confirm:
        return {
            "mode": "PREVIEW_ONLY",
            "requires_confirmation": True,
            "message": "پیش‌نمایش بازگردانی تغییرات (Revert) به نسخه پشتیبان. برای اجرا نیاز به --confirm است.",
            "plan": plan,
        }

    # 1. Restore missing rules first (so access is never interrupted)
    for spec in rules_to_restore:
        client.horizon_add_sg_rule(spec)

    # 2. Restore server SG memberships
    for srv_item in servers_to_restore:
        client.horizon_update_server_sgs(srv_item["server_id"], srv_item["restore_sg_ids"])

    # 3. Delete rules that were added after backup
    for del_item in rules_to_delete:
        client.horizon_delete_sg_rule(del_item["security_group_id"], del_item["rule_id"])

    write_audit_log("system.revert", str(target_path.name), plan, "SUCCESS")
    return {
        "mode": "EXECUTED",
        "success": True,
        "message": f"تمام تغییرات با موفقیت به وضعیت نسخه پشتیبان ({target_path.name}) بازگردانی شد.",
        "details": plan,
    }


def cmd_audit_history(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    if not AUDIT_LOG_PATH.exists():
        return {"count": 0, "entries": []}
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    entries = []
    for line in lines[-args.limit :]:
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return {"count": len(entries), "entries": entries}


def cmd_init(client: OpenStackClient, args: argparse.Namespace) -> Dict[str, Any]:
    """Initialize configuration (.env) for any user's OpenStack cloud."""
    import getpass

    target_env = Path(args.output) if args.output else (Path.cwd() / ".env")
    horizon_url = (args.horizon_url or "").strip()
    auth_url = (args.auth_url or "").strip()
    username = (args.username or "").strip()
    password = args.password or ""
    domain = (args.domain or "Default").strip()
    region = (args.region or "default").strip()
    project_name = (args.project_name or "").strip()
    project_id = (args.project_id or "").strip()
    verify_ssl = "false" if args.insecure else "true"

    if sys.stdin.isatty() and not (horizon_url and username and password):
        sys.stderr.write("=== راه‌اندازی اتصال AiStackOps به OpenStack ===\n")
        if not horizon_url:
            horizon_url = input("آدرس پنل وب OpenStack Horizon (مثال: https://panel.example.com): ").strip()
        if not username:
            username = input("نام کاربری (OS_USERNAME): ").strip()
        if not password:
            password = getpass.getpass("رمز عبور (OS_PASSWORD): ").strip()
        dom_in = input(f"دامنه کاربری (OS_USER_DOMAIN_NAME) [{domain}]: ").strip()
        if dom_in:
            domain = dom_in
        reg_in = input(f"نام ریجن (OS_REGION_NAME) [{region}]: ").strip()
        if reg_in:
            region = reg_in

    if not horizon_url:
        raise OpenStackError(
            "آدرس پنل (OS_HORIZON_URL) الزامی است. با فلگ --horizon-url وارد کنید یا در محیط تعاملی اجرا نمایید.",
            code="MISSING_HORIZON_URL",
        )

    target_env.parent.mkdir(parents=True, exist_ok=True)
    env_content = (
        "# AiStackOps - OpenStack Connection Configuration\n"
        f'OS_HORIZON_URL="{horizon_url.rstrip("/")}"\n'
        f'OS_AUTH_URL="{auth_url.rstrip("/")}"\n'
        f'OS_USERNAME="{username}"\n'
        f'OS_PASSWORD="{password}"\n'
        f'OS_USER_DOMAIN_NAME="{domain}"\n'
        f'OS_REGION_NAME="{region}"\n'
        f'OS_PROJECT_NAME="{project_name}"\n'
        f'OS_PROJECT_ID="{project_id}"\n'
        f'OS_VERIFY_SSL="{verify_ssl}"\n'
    )
    target_env.write_text(env_content, encoding="utf-8")
    try:
        os.chmod(target_env, 0o600)
    except Exception:
        pass

    test_cfg = load_config(target_env)
    test_client = OpenStackClient(test_cfg)
    auth_info = None
    auth_error = None
    if username and password:
        try:
            auth_info = test_client.authenticate(force_refresh=True)
        except Exception as e:
            auth_error = str(e)

    return {
        "ok": auth_error is None,
        "config_file": str(target_env),
        "authenticated": auth_info is not None,
        "session": auth_info,
        "auth_error": auth_error,
        "message": f"فایل تنظیمات در '{target_env}' با دسترسی امن (600) ذخیره شد.",
    }


# ==============================================================================
# CLI Argument Parser
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osctl",
        description="ابزار جامع و امن مدیریت زیرساخت ابری OpenStack (با خواندن خودکار .env)",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="مسیر فایل .env")
    sub = parser.add_subparsers(dest="command", required=True)

    # 0. init
    p_init = sub.add_parser("init", help="راه‌اندازی و ذخیره تنظیمات اتصال به هر کلود OpenStack")
    p_init.add_argument("--horizon-url", help="آدرس پنل وب OpenStack Horizon (مثال: https://panel.example.com)")
    p_init.add_argument("--auth-url", default="", help="آدرس Keystone (اختیاری)")
    p_init.add_argument("--username", help="نام کاربری (OS_USERNAME)")
    p_init.add_argument("--password", help="رمز عبور (OS_PASSWORD)")
    p_init.add_argument("--domain", default="Default", help="دامنه کاربری (OS_USER_DOMAIN_NAME)")
    p_init.add_argument("--region", default="default", help="نام ریجن (OS_REGION_NAME)")
    p_init.add_argument("--project-name", default="", help="نام پروژه (OS_PROJECT_NAME)")
    p_init.add_argument("--project-id", default="", help="شناسه پروژه (OS_PROJECT_ID)")
    p_init.add_argument("--insecure", action="store_true", help="غیرفعال‌سازی بررسی گواهی SSL")
    p_init.add_argument("--output", default="", help="مسیر ذخیره فایل .env (پیش‌فرض: ./.env)")
    p_init.set_defaults(func=cmd_init)

    # 1. auth-check
    p_auth = sub.add_parser("auth-check", help="بررسی اتصال و صحت اطلاعات احراز هویت در .env")
    p_auth.add_argument("--refresh", action="store_true", help="دریافت نشست جدید بدون استفاده از کش")
    p_auth.set_defaults(func=cmd_auth_check)

    # 2. overview
    p_ov = sub.add_parser("overview", help="نمای کلی از سرورها، سکیوریتی گروپ‌ها، آی‌پی‌ها و دیسک‌ها")
    p_ov.set_defaults(func=cmd_overview)

    # 3. servers
    p_srv = sub.add_parser("servers", help="مدیریت سرورها و ماشین‌های مجازی (Nova)")
    srv_sub = p_srv.add_subparsers(dest="srv_cmd", required=True)

    p_srv_list = srv_sub.add_parser("list", help="لیست تمام سرورها")
    p_srv_list.set_defaults(func=cmd_servers_list)

    p_srv_find = srv_sub.add_parser("find", help="جستجوی هوشمند سرور با نام، بخشی از نام، آی‌پی یا شناسه")
    p_srv_find.add_argument("query", help="نام، آی‌پی یا شناسه سرور")
    p_srv_find.set_defaults(func=cmd_servers_find)

    p_srv_show = srv_sub.add_parser("show", help="نمایش جزئیات کامل سرور، پورت‌ها و سکیوریتی گروپ‌های متصل")
    p_srv_show.add_argument("server", help="نام، آی‌پی یا شناسه سرور")
    p_srv_show.set_defaults(func=cmd_servers_show)

    p_srv_diag = srv_sub.add_parser("diagnose", help="عیب‌یابی هوشمند سرور (وضعیت، شبکه، آی‌پی عمومی و پورت‌های باز فایروال)")
    p_srv_diag.add_argument("server", help="نام، آی‌پی یا شناسه سرور")
    p_srv_diag.set_defaults(func=cmd_servers_diagnose)

    for act in (
        "start",
        "stop",
        "reboot-soft",
        "reboot-hard",
        "pause",
        "unpause",
        "suspend",
        "resume",
        "resize-confirm",
        "resize-revert",
    ):
        p_act = srv_sub.add_parser(act, help=f"عملیات {act} روی سرور")
        p_act.add_argument("server", help="نام، آی‌پی یا شناسه سرور")
        p_act.add_argument("--confirm", action="store_true", help="تایید اجرای نهایی عملیات")
        p_act.set_defaults(func=cmd_servers_action, action_type=act)

    p_srv_addsg = srv_sub.add_parser("attach-sg", help="اتصال یک سکیوریتی گروپ به سرور")
    p_srv_addsg.add_argument("server", help="نام، آی‌پی یا شناسه سرور")
    p_srv_addsg.add_argument("--sg", required=True, help="نام یا شناسه سکیوریتی گروپ")
    p_srv_addsg.add_argument("--confirm", action="store_true")
    p_srv_addsg.set_defaults(func=cmd_servers_sg_membership, op="attach")

    p_srv_remsg = srv_sub.add_parser("detach-sg", help="جداسازی یک سکیوریتی گروپ از سرور")
    p_srv_remsg.add_argument("server", help="نام، آی‌پی یا شناسه سرور")
    p_srv_remsg.add_argument("--sg", required=True, help="نام یا شناسه سکیوریتی گروپ")
    p_srv_remsg.add_argument("--confirm", action="store_true")
    p_srv_remsg.set_defaults(func=cmd_servers_sg_membership, op="detach")

    p_srv_del = srv_sub.add_parser("delete", help="حذف دائمی سرور")
    p_srv_del.add_argument("server", help="نام، آی‌پی یا شناسه سرور")
    p_srv_del.add_argument("--confirm", action="store_true")
    p_srv_del.set_defaults(func=cmd_servers_delete)

    # 4. sg (Security Groups)
    p_sg = sub.add_parser("sg", help="مدیریت سکیوریتی گروپ‌ها و قوانین فایروال (Neutron)")
    sg_sub = p_sg.add_subparsers(dest="sg_cmd", required=True)

    p_sg_list = sg_sub.add_parser("list", help="لیست تمام سکیوریتی گروپ‌های پروژه")
    p_sg_list.add_argument("--with-rules", action="store_true", help="نمایش قوانین هر سکیوریتی گروپ")
    p_sg_list.set_defaults(func=cmd_sg_list)

    p_sg_audit = sg_sub.add_parser("audit", help="ممیزی هوشمند امنیت فایروال (یافتن پورت‌های حساس باز به اینترنت، قوانین تکراری و گروه‌های بدون استفاده)")
    p_sg_audit.set_defaults(func=cmd_sg_audit)

    p_sg_srv = sg_sub.add_parser("for-server", help="بررسی تمام سکیوریتی گروپ‌ها و پورت‌های متصل به یک سرور خاص")
    p_sg_srv.add_argument("server", help="نام، آی‌پی یا شناسه سرور")
    p_sg_srv.set_defaults(func=cmd_sg_for_server)

    p_sg_show = sg_sub.add_parser("show", help="نمایش جزئیات و قوانین یک سکیوریتی گروپ")
    p_sg_show.add_argument("sg", help="نام یا شناسه سکیوریتی گروپ")
    p_sg_show.add_argument("--server", help="محدود کردن جستجو به سکیوریتی گروپ‌های این سرور")
    p_sg_show.set_defaults(func=cmd_sg_show)

    p_sg_chk = sg_sub.add_parser("check-access", help="بررسی اینکه آیا یک آی‌پی به یک سرور دسترسی دارد یا خیر")
    p_sg_chk.add_argument("--server", required=True, help="نام، آی‌پی یا شناسه سرور")
    p_sg_chk.add_argument("--ip", required=True, help="آی‌پی یا CIDR مورد بررسی")
    p_sg_chk.add_argument("--port", help="شماره پورت یا نام سرویس (اختیاری)")
    p_sg_chk.add_argument("--protocol", default="tcp", help="پروتکل (tcp, udp, icmp, any)")
    p_sg_chk.add_argument("--direction", default="ingress", choices=["ingress", "egress"])
    p_sg_chk.set_defaults(func=cmd_sg_check_access)

    p_sg_allow = sg_sub.add_parser("allow", help="دادن دسترسی (افزودن Rule جدید) به یک سکیوریتی گروپ")
    p_sg_allow.add_argument("--sg", required=True, help="نام یا شناسه سکیوریتی گروپ مقصد")
    p_sg_allow.add_argument("--server", help="نام یا شناسه سرور (برای اعتبارسنجی تعلق سکیوریتی گروپ به سرور)")
    p_sg_allow.add_argument("--ip", required=True, help="آی‌پی یا رنج CIDR (مثال: 1.2.3.4 یا 1.2.3.0/24 یا any)")
    p_sg_allow.add_argument(
        "--port",
        default="all",
        help="پورت، بازه پورت یا نام سرویس (مثال: 22 یا ssh یا 80,443 یا web یا 8000:8080 یا all)",
    )
    p_sg_allow.add_argument("--protocol", default="tcp", help="پروتکل: tcp, udp, icmp, ping, any (پیش‌فرض: tcp)")
    p_sg_allow.add_argument("--direction", default="ingress", choices=["ingress", "egress"])
    p_sg_allow.add_argument("--description", default="", help="توضیحات قانون (اختیاری)")
    p_sg_allow.add_argument("--confirm", action="store_true", help="تایید ثبت نهایی قانون")
    p_sg_allow.set_defaults(func=cmd_sg_allow)

    p_sg_revoke = sg_sub.add_parser("revoke", help="لغو و حذف دسترسی از یک سکیوریتی گروپ")
    p_sg_revoke.add_argument("--rule-id", help="شناسه دقیق قانون (Rule ID)")
    p_sg_revoke.add_argument("--sg", help="نام یا شناسه سکیوریتی گروپ")
    p_sg_revoke.add_argument("--server", help="نام یا شناسه سرور")
    p_sg_revoke.add_argument("--ip", help="آی‌پی یا CIDR برای یافتن و حذف قانون")
    p_sg_revoke.add_argument("--port", help="پورت (اختیاری)")
    p_sg_revoke.add_argument("--protocol", help="پروتکل (اختیاری)")
    p_sg_revoke.add_argument("--confirm", action="store_true", help="تایید حذف نهایی")
    p_sg_revoke.set_defaults(func=cmd_sg_revoke)

    # 5. network & fip
    p_net = sub.add_parser("networks", help="لیست شبکه‌ها و ساب‌نت‌ها")
    p_net.set_defaults(func=cmd_network_list)

    p_fip = sub.add_parser("fip", help="مدیریت آی‌پی‌های عمومی (Floating IPs)")
    fip_sub = p_fip.add_subparsers(dest="fip_cmd", required=True)

    p_fip_list = fip_sub.add_parser("list", help="لیست تمام آی‌پی‌های عمومی پروژه")
    p_fip_list.set_defaults(func=cmd_fip_list)

    # 6. volumes
    p_vol = sub.add_parser("volumes", help="مدیریت دیسک‌ها و فضای ذخیره‌سازی (Cinder)")
    vol_sub = p_vol.add_subparsers(dest="vol_cmd", required=True)

    p_vol_list = vol_sub.add_parser("list", help="لیست تمام دیسک‌ها")
    p_vol_list.set_defaults(func=cmd_volumes_list)

    # 7. flavors, images, quotas, audit
    p_flv = sub.add_parser("flavors", help="لیست تیپ‌های سخت‌افزاری (Flavors)")
    p_flv.set_defaults(func=cmd_flavors_list)

    p_img = sub.add_parser("images", help="لیست سیستم‌عامل‌ها و اسنپ‌شات‌ها (Images)")
    p_img.set_defaults(func=cmd_images_list)

    p_qta = sub.add_parser("quotas", help="نمایش سهمیه و میزان مصرف منابع پروژه")
    p_qta.set_defaults(func=cmd_quotas_show)

    p_aud = sub.add_parser("audit-log", help="نمایش تاریخچه عملیات انجام‌شده")
    p_aud.add_argument("--limit", type=int, default=50)
    p_aud.set_defaults(func=cmd_audit_history)

    p_bak = sub.add_parser("backup", help="گرفتن نسخه پشتیبان کامل از وضعیت سکیوریتی گروپ‌ها و سرورها قبل از تغییر")
    p_bak.add_argument("--label", default="before_changes", help="برچسب نسخه پشتیبان")
    p_bak.set_defaults(func=cmd_backup_create)

    p_baks = sub.add_parser("backups", help="لیست نسخه‌های پشتیبان ذخیره‌شده")
    p_baks.set_defaults(func=cmd_backups_list)

    p_rev = sub.add_parser("revert", help="بازگردانی سریع تمامی تغییرات به وضعیت نسخه پشتیبان (Revert)")
    p_rev.add_argument("--file", default="", help="مسیر یا نام فایل پشتیبان (پیش‌فرض: آخرین نسخه پشتیبان)")
    p_rev.add_argument("--confirm", action="store_true", help="تایید اجرای بازگردانی")
    p_rev.set_defaults(func=cmd_revert)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = load_config(Path(args.env_file))
        client = OpenStackClient(config)
        result = args.func(client, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except OpenStackError as e:
        err_payload = {
            "ok": False,
            "error_code": e.code,
            "message": e.message,
        }
        if e.status_code is not None:
            err_payload["http_status"] = e.status_code
        if e.details is not None:
            err_payload["details"] = e.details
        print(json.dumps(err_payload, ensure_ascii=False, indent=2))
        return 1
    except Exception as e:
        err_payload = {
            "ok": False,
            "error_code": "UNEXPECTED_ERROR",
            "message": str(e),
        }
        print(json.dumps(err_payload, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
