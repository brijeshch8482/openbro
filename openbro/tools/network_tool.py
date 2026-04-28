"""Network info tool - ping, IP info, connectivity check."""

import platform
import socket
import subprocess

import httpx

from openbro.tools.base import BaseTool, RiskLevel


class NetworkTool(BaseTool):
    name = "network"
    description = "Network operations: ping, public IP, local IP, DNS lookup, connectivity check"
    risk = RiskLevel.SAFE

    def run(self, action: str, target: str = "") -> str:
        action = action.lower().strip()
        if action == "ping":
            return self._ping(target or "8.8.8.8")
        elif action in ("ip", "public_ip"):
            return self._public_ip()
        elif action == "local_ip":
            return self._local_ip()
        elif action == "dns":
            return self._dns(target)
        elif action == "check":
            return self._connectivity_check()
        else:
            return f"Unknown action: {action}. Available: ping, ip, local_ip, dns, check"

    def _ping(self, host: str) -> str:
        system = platform.system()
        try:
            count_flag = "-n" if system == "Windows" else "-c"
            result = subprocess.run(
                ["ping", count_flag, "4", host],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.stdout
        except Exception as e:
            return f"Ping failed: {e}"

    def _public_ip(self) -> str:
        try:
            resp = httpx.get("https://api.ipify.org?format=json", timeout=10)
            ip = resp.json().get("ip", "unknown")
            return f"Public IP: {ip}"
        except Exception as e:
            return f"Could not fetch public IP: {e}"

    def _local_ip(self) -> str:
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            return f"Hostname: {hostname}\nLocal IP: {local_ip}"
        except Exception as e:
            return f"Could not get local IP: {e}"

    def _dns(self, domain: str) -> str:
        if not domain:
            return "Domain required for DNS lookup"
        try:
            ip = socket.gethostbyname(domain)
            return f"{domain} -> {ip}"
        except socket.gaierror as e:
            return f"DNS lookup failed for {domain}: {e}"

    def _connectivity_check(self) -> str:
        results = []
        for host in ("8.8.8.8", "google.com"):
            try:
                socket.create_connection((host, 80 if host == "google.com" else 53), timeout=3)
                results.append(f"  {host}: reachable")
            except Exception as e:
                results.append(f"  {host}: unreachable ({e})")
        return "Connectivity check:\n" + "\n".join(results)

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["ping", "ip", "local_ip", "dns", "check"],
                        "description": "Network action to perform",
                    },
                    "target": {
                        "type": "string",
                        "description": "Target host/domain (for ping or dns)",
                    },
                },
                "required": ["action"],
            },
        }
