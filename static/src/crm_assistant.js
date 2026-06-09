/** @odoo-module **/

import { registry } from "@web/core/registry";
import { FloatingWidget } from "./components/chat_widget";

registry.category("main_components").add(
    "crm_assistant.FloatingWidget",
    {
        Component: FloatingWidget,
    }
);
