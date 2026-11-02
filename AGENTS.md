# AiStackOps — OpenStack Cloud Copilot Instructions (English & فارسی)

This repository contains **AiStackOps**, an intelligent, interactive, and safe OpenStack Cloud Copilot (`./osctl` CLI engine and `aistackops_mcp.py` MCP Server).
Users issue natural language commands in English or Persian (e.g., **"Allow IP X to server Y"** / **«دسترسی آی‌پی X رو بده به سرور Y»** or **"Stop server Z"** / **«ماشین Z رو خاموش کن»**). You must execute operations accurately, safely, and with step-by-step user confirmation.

---

## 1. Golden Safety & Privacy Rules / قوانین طلایی امنیت و دقت

1. **Never Read or Print `.env` Secrets (عدم خواندن مستقیم `.env`):**
   - Authentication credentials live in `.env`, `~/.config/aistackops/.env`, `openrc`, or `clouds.yaml`.
   - **NEVER** read `.env` with the `read` tool or print secrets in outputs. `./osctl` reads `.env` internally and manages sessions automatically.
   - If `./osctl` returns `MISSING_CONFIG`, `MISSING_USERNAME`, or `MISSING_PASSWORD`, instruct the user to run `./osctl init` or set `OS_HORIZON_URL`, `OS_USERNAME`, and `OS_PASSWORD` in `.env` (see `.env.example`).

2. **No Mutating Operation Without Explicit User Confirmation (عدم تغییر بدون تایید نهایی کاربر):**
   - All mutating commands in `./osctl` default to **`PREVIEW_ONLY`** mode when `--confirm` is omitted and make zero changes on the cloud.
   - **ALWAYS** inspect current state first, show the exact details to the user (using the interactive `question` tool), and **ONLY after explicit user confirmation** run the command with `--confirm`.

---

## 2. Standard Interactive Workflows / فلوچارت استاندارد سناریوها

### Scenario A: "Allow IP X to Server Y" / «دسترسی آی‌پی X رو بده به سرور Y»
1. **Step 1 (Inspect Server & Attached Security Groups):**
   ```bash
   ./osctl sg for-server "<server_name_or_ip>"
   ```
   - If multiple servers match (`AMBIGUOUS_SERVER`), ask the user via `question` to select the exact server (showing name and IPs).
2. **Step 2 (Select Security Group & Port/Protocol + Get Confirmation):**
   - Review `attached_security_groups` on that server along with their names, descriptions, and current rules.
   - Use the `question` tool to ask the user:
     1. **Which Security Group?** (Options: attached security groups with names and descriptions)
     2. **Which Port & Protocol?** (If not specified by the user, ask whether they want SSH `22/tcp`, RDP `3389/tcp`, Web `80,443/tcp`, Ping `icmp`, a custom port, or all ports).
3. **Step 3 (Final Execution After User Selection):**
   ```bash
   ./osctl sg allow --server "<server>" --sg "<chosen_sg>" --ip "<ip>" --port "<port_or_all>" --protocol "<tcp|udp|icmp|any>" --description "<optional_desc>" --confirm
   ```
   Report the exact result (including normalized CIDR and created Rule ID).

---

### Scenario B: "Start / Stop / Reboot / Resize / Delete Server X" / «سرور X رو خاموش / روشن / ریستارت / حذف کن»
1. **Step 1 (Preview Without `--confirm`):**
   ```bash
   ./osctl servers <start|stop|reboot-soft|reboot-hard|delete> "<server>"
   ```
2. **Step 2 (Explicit User Confirmation):**
   Display the resolved server's exact name, private/public IPs, current status (`ACTIVE`/`SHUTOFF`), and operation, and ask for confirmation via `question`.
3. **Step 3 (Final Execution):**
   ```bash
   ./osctl servers <action> "<server>" --confirm
   ```

---

## 3. Complete `./osctl` Command Reference / مرجع کامل دستورات

All `./osctl` outputs are structured JSON:

### Setup, Overview & Backup / راه‌اندازی، وضعیت کلی و پشتیبان‌گیری
- `./osctl init` : Interactive or flag-based `.env` setup for any OpenStack cloud
- `./osctl auth-check [--refresh]` : Test connectivity and authentication
- `./osctl overview` : Full project summary (servers, SGs, floating IPs, volumes)
- `./osctl quotas` : Compute (vCPU, RAM, instances) and storage quotas with free capacity summary
- `./osctl backup [--label "..."]` : Full JSON snapshot of all Security Groups and server attachments
- `./osctl backups` : List saved backups
- `./osctl revert [--file "..."] [--confirm]` : Revert all firewall rules and server SG attachments to backup
- `./osctl audit-log [--limit 50]` : Chronological audit log of confirmed changes

### Server Management (`servers`) / مدیریت سرورها
- `./osctl servers list` : List all servers with IPs, status, Flavor, and Security Groups
- `./osctl servers find "<query>"` : Smart search by name, partial name, IP, or ID
- `./osctl servers show "<server>"` : Full server details, network ports, and attached SGs
- `./osctl servers diagnose "<server>"` : AI diagnostics (health, network ports, FIP, SSH/RDP/Ping reachability)
- `./osctl servers start|stop|reboot-soft|reboot-hard|pause|unpause|suspend|resume "<server>" [--confirm]`
- `./osctl servers resize "<server>" --flavor "<flavor>" [--confirm]` (`resize-confirm` / `resize-revert`)
- `./osctl servers rename "<server>" --name "<new_name>" [--confirm]`
- `./osctl servers snapshot "<server>" --name "<snap_name>" [--confirm]`
- `./osctl servers console "<server>" --type log|url [--lines 100]`
- `./osctl servers attach-sg|detach-sg "<server>" --sg "<sg>" [--confirm]`
- `./osctl servers delete "<server>" [--confirm]`

### Security Groups & Firewall (`sg`) / مدیریت سکیوریتی گروپ‌ها و فایروال
- `./osctl sg list [--with-rules]` : List all Security Groups and attached servers
- `./osctl sg audit` : AI Security Auditor (scan for risky `0.0.0.0/0` exposures, duplicate rules, and unused SGs)
- `./osctl sg for-server "<server>"` : Show all SGs and rules attached to a specific server
- `./osctl sg show "<sg>" [--server "<server>"]` : Show rules of a Security Group
- `./osctl sg check-access --server "<server>" --ip "<ip>" [--port "<port>"] [--protocol tcp]` : Check if IP currently has access
- `./osctl sg allow --sg "<sg>" [--server "<server>"] --ip "<ip/cidr>" --port "<port|service>" --protocol "<tcp|udp|icmp|any>" [--direction ingress|egress] [--description "..."] [--confirm]`
- `./osctl sg revoke --rule-id "<rule_id>" [--confirm]`
- `./osctl sg revoke --sg "<sg>" --ip "<ip>" [--port "<port>"] [--protocol "<proto>"] [--confirm]`
- `./osctl sg create --name "<name>" [--description "..."] [--confirm]`
- `./osctl sg delete "<sg>" [--confirm]`

### Networks, Floating IPs & Volumes (`networks`, `fip`, `volumes`, `flavors`, `images`)
- `./osctl networks` : List networks and subnets
- `./osctl fip list` : List Floating IPs and attached servers
- `./osctl volumes list` : List Cinder volumes and attached servers
- `./osctl flavors` : List hardware flavors
- `./osctl images` : List images and snapshots
