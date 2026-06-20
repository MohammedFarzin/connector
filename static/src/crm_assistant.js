odoo.define("connector.crm_assistant", function (require) {
    "use strict";

    const { registry } = require("@web/core/registry");
    const { FloatingWidget } = require("connector.components.chat_widget");

    registry.category("main_components").add(
        "crm_assistant.FloatingWidget",
        {
            Component: FloatingWidget,
        }
    );

    return {};
});
