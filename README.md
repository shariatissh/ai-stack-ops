<div align="center">

# ☁️🤖 AiStackOps (`osctl`)

**Universal AI-Native OpenStack Cloud Copilot — MCP Server, Agent Skill & Safe CLI Engine**  
**دستیار هوشمند، تعاملی و ایمن مدیریت زیرساخت ابری OpenStack (مبتنی بر MCP، Skill و خط فرمان)**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![OpenStack](https://img.shields.io/badge/OpenStack-Nova%20%7C%20Neutron%20%7C%20Cinder%20%7C%20Horizon-ED1944.svg?logo=openstack&logoColor=white)](https://www.openstack.org/)
[![MCP Compatible](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-10a37f.svg)](https://modelcontextprotocol.io/)
[![AI Agents](https://img.shields.io/badge/Agents-OpenCode%20%7C%20Claude%20%7C%20Cursor%20%7C%20ChatGPT-purple.svg)](#ai-integrations-mcp--skills)

[🇬🇧 English Documentation](#-english-documentation) • [🇮🇷 مستندات فارسی](#-مستندات-فارسی)

</div>

---

## 🇬🇧 English Documentation

### What is AiStackOps?

**AiStackOps** turns your AI assistant (**OpenCode**, **Claude Code**, **Claude Desktop**, **Cursor**, **Windsurf**, or **ChatGPT**) into a **Senior OpenStack DevOps & Cloud Security Engineer**.

Instead of navigating complex Horizon dashboards or memorizing long `openstack` CLI UUIDs, you simply talk to your AI in natural language (English, Persian, or any language):
- *"Allow IP `203.0.113.50` to access server `web-prod`"*
- *"Scan all our security groups for risky public `0.0.0.0/0` exposures"*
- *"Why can't I SSH into `app-server-02`? Diagnose it."*
- *"Take a backup of all firewall rules before we clean them up, and revert if anything breaks."*

### ✨ Key Differentiators & AI Safety Guardrails

1. **Mandatory `PREVIEW_ONLY` Human-in-the-Loop Safety:**  
   Every mutating action (opening/revoking firewall rules, stopping/rebooting/deleting VMs, detaching SGs, reverting snapshots) runs in **Preview Mode** by default. The AI inspects the live state, shows you the exact plan, asks clarifying questions (e.g., *which Security Group?* and *which Port/Protocol?*), and **only executes with `--confirm` after your explicit approval**.
2. **Zero-Secret Exposure Architecture:**  
   Credentials live strictly in `.env`, `openrc`, or `clouds.yaml`. The AI agent never reads or prints your passwords or session tokens.
3. **Firewall-Bypassing Horizon Gateway + Standard OpenStack Support:**  
   Works seamlessly over HTTPS (port 443) via OpenStack Horizon Web/REST APIs (ideal when ports `5000/8774/9696` are firewalled) as well as standard OpenStack environment configurations.
4. **Instant Infrastructure State Backup & 1-Click Revert:**  
   Snapshot all Security Groups, rules, and server attachments to JSON before risky changes (`./osctl backup`) and roll back any drift in seconds (`./osctl revert --confirm`).
5. **AI Security Auditor & Smart Diagnostics:**  
   Built-in `sg audit` detects critical `0.0.0.0/0` exposures (SSH, RDP, Databases, Docker, etc.), duplicate rules, and unused Security Groups. Built-in `servers diagnose` checks VM power state, network ports, floating IPs, and ingress reachability in one shot.

---

### 🚀 Quick Start (Connect Any OpenStack Cloud)

#### 1. Clone & Install
```bash
git clone https://github.com/shariatissh/ai-stack-ops.git
cd ai-stack-ops
pip install -e .
```

#### 2. Configure Your OpenStack Connection
Choose any of the 3 supported methods:

- **Method A: Interactive Setup Wizard**
  ```bash
  ./osctl init
  ```
  Or non-interactively:
  ```bash
  ./osctl init \
    --horizon-url "https://horizon.example.com" \
    --username "your_username" \
    --password "your_password"
  ```

- **Method B: `.env` File**
  ```bash
  cp .env.example .env
  chmod 600 .env
  # Edit .env and set OS_HORIZON_URL, OS_USERNAME, OS_PASSWORD
  ```

- **Method C: Standard OpenStack `openrc.sh` or `clouds.yaml`**
  If you already have `OS_AUTH_URL`, `OS_USERNAME`, and `OS_PASSWORD` exported in your shell, or a `~/.config/openstack/clouds.yaml` file, `AiStackOps` detects them automatically.

Verify your connection:
```bash
./osctl auth-check
```

---

### 🤖 AI Integrations (MCP & Skills)

#### 1. Model Context Protocol (MCP) — Claude Desktop, Cursor, ChatGPT, OpenCode
`aistackops_mcp.py` is a zero-dependency **stdio JSON-RPC 2.0 MCP Server** exposing **16 cloud tools**:
- `openstack_overview`, `openstack_quotas`
- `openstack_servers_list`, `openstack_server_inspect`, `openstack_server_diagnose`, `openstack_server_action`
- `openstack_sg_list`, `openstack_sg_audit`, `openstack_sg_check_access`, `openstack_sg_allow`, `openstack_sg_revoke`
- `openstack_volumes_list`, `openstack_fip_list`, `openstack_networks_list`
- `openstack_backup`, `openstack_revert`

**Add to `claude_desktop_config.json` / Cursor MCP:**
```json
{
  "mcpServers": {
    "aistackops": {
      "command": "python3",
      "args": ["/absolute/path/to/ai-stack-ops/aistackops_mcp.py"]
    }
  }
}
```

**Add to OpenCode (`~/.config/opencode/opencode.jsonc`):**
```jsonc
{
  "mcp": {
    "aistackops": {
      "type": "local",
      "command": ["python3", "/absolute/path/to/ai-stack-ops/aistackops_mcp.py"],
      "enabled": true
    }
  }
}
```

#### 2. Native Agent Skill (OpenCode & Claude Code)
Copy the skill globally so every session knows the interactive safety workflows:
```bash
mkdir -p ~/.config/opencode/skills/aistackops ~/.claude/skills/aistackops
cp skills/aistackops/SKILL.md ~/.config/opencode/skills/aistackops/SKILL.md
cp skills/aistackops/SKILL.md ~/.claude/skills/aistackops/SKILL.md
```

---

### 🛠️ Complete CLI Reference (`./osctl`)

| Category | Command | Description |
| :--- | :--- | :--- |
| **Setup & Auth** | `./osctl init` | Interactive or flag-based `.env` setup wizard |
| | `./osctl auth-check [--refresh]` | Verify authentication and active project/region |
| **Cloud Overview** | `./osctl overview` | Full summary of VMs, Security Groups, Floating IPs, and Volumes |
| | `./osctl quotas` | CPU, RAM, Instances, and Volume storage usage & free capacity |
| **Servers (Nova)** | `./osctl servers list` | List all instances with IPs, status, flavor, and SGs |
| | `./osctl servers find "<query>"` | Smart fuzzy search by name, partial name, IP, or UUID |
| | `./osctl servers show "<server>"` | Detailed server ports and attached Security Groups |
| | `./osctl servers diagnose "<server>"` | AI health check (power state, ports, FIPs, SSH/RDP firewall reachability) |
| | `./osctl servers start\|stop\|reboot-soft\|reboot-hard "<srv>" [--confirm]` | Power lifecycle operations (Preview first, `--confirm` to execute) |
| | `./osctl servers pause\|unpause\|suspend\|resume "<srv>" [--confirm]` | Pause or suspend operations |
| | `./osctl servers resize "<srv>" --flavor "<flavor>" [--confirm]` | Resize VM hardware flavor (`resize-confirm` / `resize-revert`) |
| | `./osctl servers rename "<srv>" --name "<new_name>" [--confirm]` | Rename an instance |
| | `./osctl servers snapshot "<srv>" --name "<snap_name>" [--confirm]` | Create an instance snapshot |
| | `./osctl servers console "<srv>" --type log\|url [--lines 100]` | Fetch boot console log or VNC console URL |
| | `./osctl servers attach-sg\|detach-sg "<srv>" --sg "<sg>" [--confirm]` | Attach or detach a Security Group on a server |
| | `./osctl servers delete "<srv>" [--confirm]` | Permanently delete an instance |
| **Firewall (Neutron)** | `./osctl sg list [--with-rules]` | List all Security Groups and attached servers |
| | `./osctl sg audit` | Scan for risky `0.0.0.0/0` exposures, duplicate rules, and unused SGs |
| | `./osctl sg for-server "<server>"` | Inspect all Security Groups & rules attached to a specific server |
| | `./osctl sg show "<sg>" [--server "<srv>"]` | Show rules and attached servers for a Security Group |
| | `./osctl sg check-access --server "<srv>" --ip "<ip>" [--port "<port>"]` | Verify if an IP/CIDR currently has firewall access |
| | `./osctl sg allow --sg "<sg>" [--server "<srv>"] --ip "<ip>" --port "<port>" --protocol "<proto>" [--confirm]` | Allow IP/CIDR access (supports aliases: `ssh`, `rdp`, `http`, `https`, `web`, `mysql`, `postgres`, `redis`, `ping`, `all`) |
| | `./osctl sg revoke --rule-id "<id>" [--confirm]` | Revoke a rule by exact ID or by `--sg` + `--ip` match |
| | `./osctl sg create --name "<name>" [--description "..."] [--confirm]` | Create a new Security Group |
| | `./osctl sg delete "<sg>" [--confirm]` | Delete a Security Group |
| **Storage & Net** | `./osctl networks` / `./osctl fip list` | List Neutron networks and Floating IPs |
| | `./osctl volumes list` / `./osctl flavors` / `./osctl images` | List Cinder volumes, Nova flavors, and Glance images |
| **Safety & Audit** | `./osctl backup [--label "..."]` / `./osctl backups` | Snapshot all SGs and server attachments to JSON |
| | `./osctl revert [--file "..."] [--confirm]` | Roll back firewall rules & attachments to backup snapshot |
| | `./osctl audit-log [--limit 50]` | View chronological audit trail of confirmed changes |

---
---

<div dir="rtl">

## 🇮🇷 مستندات فارسی

### معرفی AiStackOps

**AiStackOps** یک کوپایلوت ابری هوشمند (AI-Native Cloud Copilot) برای زیرساخت‌های **OpenStack** است که به هر مهندس DevOps، مدیر سیستم یا تیم زیرساخت اجازه می‌دهد کلود اوپن‌استک خود را از طریق گفتگو به زبان طبیعی در **OpenCode، Claude (Desktop / Code)، Cursor و ChatGPT** با امنیت کامل مدیریت کند.

به جای جستجوی دستی در پنل‌های پیچیده Horizon یا حفظ کردن شناسه‌های طولانی، کافیست به زبان طبیعی بگویید:
- *«دسترسی آی‌پی `185.10.20.30` رو بده به سرور `web`»*
- *«کل سکیوریتی گروپ‌ها رو ممیزی امنیتی کن ببین چه پورت‌های حساسی به کل اینترنت (`0.0.0.0/0`) بازه»*
- *«سرور `db-01` رو عیب‌یابی کن ببین چرا بهش وصل نمیشم»*
- *«قبل از پاکسازی قوانین فایروال یه بکاپ کامل بگیر تا اگه مشکلی پیش اومد سریع Revert کنیم»*

---

### 🛡️ ویژگی‌های منحصربه‌فرد و لایه‌های امنیتی هوشمند

۱. **حالت پیش‌نمایش اجباری (`PREVIEW_ONLY`):**  
هیچ دستور تغییردهنده‌ای بدون تایید صریح شما و فلگ `--confirm` اجرا نمی‌شود. دستیار ابتدا وضعیت فعلی را بررسی می‌کند، اگر سرور چند سکیوریتی گروپ داشته باشد لیست آن‌ها را به شما نشان می‌دهد، می‌پرسد روی کدام سکیوریتی گروپ و چه پورتی اعمال شود، و **فقط پس از تایید شما** تغییر را اعمال می‌کند.

۲. **حفاظت صددرصدی از رمز عبور و اطلاعات حساس:**  
اطلاعات اتصال شما فقط در فایل محلی `.env` (یا `~/.config/aistackops/.env`) با سطح دسترسی `600` نگهداری می‌شود. هوش مصنوعی هرگز محتوای فایل `.env` را نمی‌خواند و رمز عبور در هیچ خروجی یا لاگی چاپ نمی‌شود.

۳. **پشتیبانی از هر کلود OpenStack (حتی پشت فایروال):**  
علاوه بر متغیرهای استاندارد `openrc` و `clouds.yaml`، این ابزار قادر است تمام عملیات را از طریق درگاه وب HTTPS (پورت ۴۴۳ پنل Horizon) انجام دهد؛ بنابراین حتی زمانی که پورت‌های `5000`، `8774` و `9696` از بیرون بسته باشند، بدون مشکل کار می‌کند.

۴. **پشتیبان‌گیری آنی و بازگردانی یک‌کلیکی (`backup` & `revert`):**  
پیش از هر تغییر حساس روی فایروال‌ها، وضعیت کامل قوانین و اتصالات سرورها ذخیره شده و در هر لحظه با دستور `revert` دقیقاً به نقطه قبل بازگردانده می‌شود.

---

### ⚡ راه‌اندازی سریع (برای هر سرویس‌دهنده OpenStack)

```bash
git clone https://github.com/shariatissh/ai-stack-ops.git
cd ai-stack-ops
./osctl init
```

پس از وارد کردن آدرس پنل، نام کاربری و رمز عبور، اتصال خود را تست کنید:
```bash
./osctl auth-check
```

### 🧠 فلوچارت هوشمند تعامل با کاربر

هنگامی که به دستیار می‌گویید: **«دسترسی آی‌پی X رو بده به سرور Y»**:
1. دستیار ابتدا دستور `./osctl sg for-server "Y"` را اجرا می‌کند.
2. اگر بیش از یک سرور با نام `Y` هم‌خوانی داشته باشد، لیست سرورهای مشابه را به همراه آی‌پی‌هایشان از شما می‌پرسد.
3. سپس لیست سکیوریتی گروپ‌های متصل به آن سرور را نمایش می‌دهد و از شما می‌پرسد:
   - **روی کدام سکیوریتی گروپ ثبت شود؟**
   - **چه پورت و پروتکلی باز شود؟** (SSH، RDP، Web، پینگ یا پورت دلخواه)
4. پس از انتخاب و تایید شما، قانون را با `--confirm` ثبت کرده و نتیجه دقیق را گزارش می‌دهد.

</div>

---

## 📄 License

Released under the [MIT License](LICENSE).
