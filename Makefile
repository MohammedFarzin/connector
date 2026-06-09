# ── CRM Assistant Connector ─────────────────────────────────────────────
#  Public repo (MIT) — installed on every client Odoo.
#
#  Commands:
#    make install    Symlink into Odoo addons path
#    make uninstall  Remove the symlink
# ──────────────────────────────────────────────────────────────────────────

# Path to Odoo's addons directory — override per environment
ODOO_ADDONS ?= ../../odoo/addons
TARGET         := $(ODOO_ADDONS)/crm_assistant_connector

.PHONY: install uninstall link copy help

help:
	@echo ""
	@echo "  CRM Assistant Connector — MIT-licensed thin client"
	@echo ""
	@echo "  make install     Symlink into Odoo addons"
	@echo "  make copy        Copy into Odoo addons (no symlink support)"
	@echo "  make uninstall   Remove from Odoo addons"
	@echo ""
	@echo "  Override addons path:"
	@echo "    make install ODOO_ADDONS=/opt/odoo/addons"
	@echo ""

install: link

link:
	@echo "Installing connector → $(TARGET)"
	@rm -rf "$(TARGET)"
	ln -sf "$(CURDIR)" "$(TARGET)"
	@echo "  ✅ Symlinked. Update Apps List in Odoo, then install the module."

copy:
	@echo "Copying connector → $(TARGET)"
	@rm -rf "$(TARGET)"
	cp -r "$(CURDIR)" "$(TARGET)"
	@echo "  ✅ Copied. Update Apps List in Odoo, then install the module."

uninstall:
	@rm -rf "$(TARGET)"
	@echo "  ✅ Removed $(TARGET)"
