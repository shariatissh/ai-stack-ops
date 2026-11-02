# AiStackOps — دستیار هوشمند و ایمن مدیریت ابر OpenStack (`osctl`)

**AiStackOps** یک کوپایلوت ابری (AI-Native Cloud Copilot) است که به هر کاربر، تیم DevOps یا مدیر زیرساخت امکان می‌دهد کلاستر **OpenStack** خود را از طریق زبان طبیعی در **OpenCode، Claude (Desktop / Code)، Cursor و ChatGPT** با امنیت کامل، پیش‌نمایش تغییرات (`PREVIEW_ONLY`) و قابلیت بازگردانی فوری (`Revert`) مدیریت کند.

---

## ۱. اتصال به هر زیرساخت OpenStack (Universal Setup)

این پروژه هیچ وابستگی ثابتی به یک دیتاسنتر خاص ندارد و با هر پنل OpenStack کار می‌کند. برای اتصال کلاود خود یکی از ۳ روش زیر را انتخاب کنید:

### روش اول: راه‌اندازی تعاملی سریع (`./osctl init`)
```bash
./osctl init
```
یا به صورت مستقیم:
```bash
./osctl init \
  --horizon-url "https://horizon.your-cloud.com" \
  --username "your_username" \
  --password "your_password"
```

### روش دوم: فایل `.env`
فایل نمونه را کپی کرده و مقادیر کلاود خود را در آن قرار دهید:
```bash
cp .env.example .env
chmod 600 .env
```

### روش سوم: استاندارد OpenStack (`openrc.sh` یا `clouds.yaml`)
اگر از قبل فایل `openrc.sh` را در ترمینال `source` کرده‌اید یا فایل `~/.config/openstack/clouds.yaml` دارید، **AiStackOps** به صورت خودکار آن را شناسایی می‌کند.

برای تست اتصال:
```bash
./osctl auth-check
```

---

## ۲. استفاده به عنوان Skill و MCP در هوش مصنوعی (AI Integrations)

### الف) استفاده به عنوان Skill در OpenCode و Claude Code
مهارت **AiStackOps** در مسیرهای زیر تعریف شده و به صورت خودکار توسط ایجنت بارگذاری می‌شود:
- `.opencode/skills/aistackops/SKILL.md` (فعال به صورت خودکار در OpenCode)
- `skills/aistackops/SKILL.md` (قابل کپی در `~/.claude/skills/aistackops/SKILL.md` یا `~/.config/opencode/skills/aistackops/SKILL.md` برای دسترسی سراسری در تمام پروژه‌ها)
- `AGENTS.md` / `CLAUDE.md` (دستورالعمل‌های رفتاری و امنیتی ایجنت)

### ب) اتصال به عنوان MCP Server (برای Claude Desktop، Cursor، ChatGPT و OpenCode)
فایل `aistackops_mcp.py` یک سرور استاندارد **Model Context Protocol (MCP)** بدون نیاز به هیچ کتابخانه اضافی است که ۱۲ ابزار هوشمند (`openstack_overview`, `openstack_sg_allow`, `openstack_sg_audit`, `openstack_server_diagnose`, `openstack_server_action`, `openstack_backup`, `openstack_revert` و...) را در اختیار مدل‌های هوش مصنوعی قرار می‌دهد.

نمونه تنظیم در `claude_desktop_config.json` یا تنظیمات MCP:
```json
{
  "mcpServers": {
    "aistackops": {
      "command": "python3",
      "args": ["/path/to/AiStackOps/aistackops_mcp.py"]
    }
  }
}
```

---

## ۳. نمونه دستورات گفتگویی (در چت با دستیار هوشمند)

کافیست به زبان طبیعی (فارسی یا انگلیسی) بگویید:

- **مدیریت دسترسی و فایروال (Security Groups):**
  - *«دسترسی آی‌پی `185.10.20.30` رو بده به سرور `web`»*
    *(دستیار ابتدا بررسی می‌کند کدام سکیوریتی گروپ‌ها به آن سرور متصل هستند، از شما می‌پرسد روی کدام سکیوریتی گروپ و کدام پورت اعمال شود، و پس از تایید شما انجام می‌دهد).*
  - *«بررسی کن ببین آی‌پی `91.92.93.94` به سرور `db` دسترسی داره یا نه؟»*
  - *«دسترسی آی‌پی `185.10.20.30` رو از روی سرور `web` بردار»*

- **ممیزی امنیت و عیب‌یابی هوشمند (AI Audit & Diagnostics):**
  - *«امنیت کل فایروال‌ها رو اسکن کن ببین چه پورت‌های حساسی به روی اینترنت بازه یا کدوم قوانین تکراری هستن»* (`./osctl sg audit`)
  - *«سرور `app-01` رو عیب‌یابی کن ببین چرا بهش وصل نمیشم»* (`./osctl servers diagnose "app-01"`)

- **پشتیبان‌گیری و بازگردانی آنی (Backup & Revert):**
  - *«قبل از تغییر فایروال‌ها یه بکاپ کامل بگیر»*
  - *«تغییراتی که دادیم رو برگردون به حالت قبل (Revert کن)»*

- **مدیریت ماشین‌های مجازی (Servers):**
  - *«یه وضعیت کلی از سرورهام بده»*
  - *«سرور `test-vm` رو خاموش کن»*
  - *«ماشین `10.0.0.15` رو ریستارت کن»*

---

## ۴. لایه‌های ایمنی هوشمند (AI Safety Guardrails)

1. **حالت پیش‌نمایش اجباری (`PREVIEW_ONLY`):** هیچ دستور تغییردهنده‌ای بدون تایید صریح شما و فلگ `--confirm` اجرا نمی‌شود.
2. **حفاظت کامل از رمز عبور:** هوش مصنوعی هرگز فایل `.env` را نمی‌خواند و رمز عبور در هیچ خروجی یا لاگی چاپ نمی‌شود.
3. **تشخیص خودکار قوانین تکراری و هم‌پوشان:** قبل از افزودن قانون جدید، وجود قانون مشابه یا بازتر (مثلاً `0.0.0.0/0`) بررسی و گزارش می‌شود.
4. **پشتیبان‌گیری و بازگردانی (`backup` / `revert`):** امکان ثبت اسنپ‌شات JSON از تمام قوانین فایروال و بازگردانی دقیق به نقطه قبل.
5. **ثبت لاگ ممیزی (`audit.log`):** تمام تغییرات تاییدشده با زمان دقیق ثبت می‌شوند.
